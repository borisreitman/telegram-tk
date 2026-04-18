#!/usr/bin/env python3
"""
Private DM SQLite cache and Telegram sync (1:1 user chats).

Use **telegram-tk search** for read-only cache search (1:1 message bodies only); **telegram-tk rescan**
or **full-rescan** to pull from Telegram. **Rescan** (with ``--recent-peer-limit N``) updates
**channel / basic-group** rows in **chats** only for dialogs in the **first N** slots returned by
``iter_dialogs`` (same ``N`` as the private-chat window). **Full-rescan** updates **chats** for
**every** channel and group. **User/bot** rows in **chats** are still updated for each user dialog
visited. Only **private user** chats get rows in **messages**.
Within the top-N rescan window, peers whose
cache is only your outgoing (**from_me**) are skipped unless **--rescan-top-all**.
No cache yet, legacy rows without **from_me**, or any cached incoming message → that
peer is fetched. **Deleted-account** peers (`User.deleted`) are skipped on sync, purged
from the cache, and never returned by search. Search reads the DB excluding those ids.

Output: by default **space-padded** columns to stdout — one row per **person** who has
a matching message (`peer_user_id`, `display_name`), newest match first. With
**--verbose**, TSV (tab-separated) per message row (`peer_user_id`, `display_name`,
`message_id`, `date_utc`, `text`), newest first. **display_name** is the **name**
Telegram shows for that private chat (first/last from the account), not **@username**
(that stays in the `username` column when present).

CLI (**telegram-tk**):

  telegram-tk auth
  telegram-tk search "invoice paid"
  telegram-tk search "keyword" --verbose
  telegram-tk rescan [--recent-peer-limit 20] [--notrace]
  telegram-tk full-rescan [--notrace]
  telegram-tk show 15840524
  telegram-tk name "ivan"
  telegram-tk channel-member @Channel --id 123

Importable as a library (**search_local**, **refresh_cache**, **show_peer**).
``python -m telegram_toolkit`` prepends ``search`` when the first CLI token is not a
subcommand (legacy ``…py "query"`` style).

**rescan** / **full-rescan** print progress on stderr (TTY spinner or one line per DM); use **--notrace** to hide that.
"""
from __future__ import annotations

import asyncio
import csv
import sqlite3
import sys
import time
from pathlib import Path

from telethon import utils
from telethon.tl.types import Channel, Chat, User

from telegram_toolkit._paths import REPO_ROOT
from telegram_toolkit.client import make_client

DEFAULT_CACHE = REPO_ROOT / ".cache" / "private_dm_messages.sqlite"


def _one_line(text: str) -> str:
    return (text or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")


def _private_chat_label(peer: User) -> str:
    """Chat-list style **name** only (first/last); Telegram does not put @username there."""
    if peer.deleted:
        return "Deleted Account"
    return utils.get_display_name(peer)


def _open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            peer_user_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            date_utc TEXT NOT NULL,
            username TEXT,
            display_name TEXT,
            text TEXT,
            PRIMARY KEY (peer_user_id, message_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_peer ON messages(peer_user_id)"
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    if "from_me" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN from_me INTEGER")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS deleted_peers (
            peer_user_id INTEGER PRIMARY KEY NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chats (
            peer_kind TEXT NOT NULL,
            peer_id INTEGER NOT NULL,
            title TEXT,
            username TEXT,
            updated_utc TEXT NOT NULL,
            PRIMARY KEY (peer_kind, peer_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS channel_member_snapshots (
            channel_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            sort_bucket INTEGER NOT NULL,
            joined_utc TEXT NOT NULL,
            PRIMARY KEY (channel_id, user_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cms_channel ON channel_member_snapshots(channel_id)"
    )
    _cms_cols = {row[1] for row in conn.execute("PRAGMA table_info(channel_member_snapshots)")}
    if _cms_cols and "is_bot" in _cms_cols:
        conn.execute(
            """
            CREATE TABLE channel_member_snapshots_new (
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                sort_bucket INTEGER NOT NULL,
                joined_utc TEXT NOT NULL,
                PRIMARY KEY (channel_id, user_id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO channel_member_snapshots_new (
                channel_id, user_id, username, first_name, last_name, sort_bucket, joined_utc
            )
            SELECT channel_id, user_id, username, first_name, last_name, sort_bucket, joined_utc
            FROM channel_member_snapshots
            """
        )
        conn.execute("DROP TABLE channel_member_snapshots")
        conn.execute("ALTER TABLE channel_member_snapshots_new RENAME TO channel_member_snapshots")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cms_channel ON channel_member_snapshots(channel_id)"
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS channel_member_snapshot_meta (
            channel_id INTEGER PRIMARY KEY NOT NULL,
            fetched_at_utc TEXT NOT NULL,
            row_count INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO deleted_peers (peer_user_id)
        SELECT DISTINCT peer_user_id FROM messages
        WHERE TRIM(COALESCE(display_name, '')) = 'Deleted Account'
        """
    )
    conn.execute(
        "DELETE FROM messages WHERE peer_user_id IN (SELECT peer_user_id FROM deleted_peers)"
    )
    if conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0] == 0:
        conn.execute(
            """
            INSERT OR REPLACE INTO chats (peer_kind, peer_id, title, username, updated_utc)
            SELECT 'user', m.peer_user_id,
                   MAX(m.display_name), MAX(m.username), datetime('now')
            FROM messages m
            WHERE m.peer_user_id NOT IN (SELECT peer_user_id FROM deleted_peers)
            GROUP BY m.peer_user_id
            """
        )
    conn.commit()
    return conn


def _purge_deleted_peer(conn: sqlite3.Connection, peer_user_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO deleted_peers (peer_user_id) VALUES (?)",
        (peer_user_id,),
    )
    conn.execute("DELETE FROM messages WHERE peer_user_id = ?", (peer_user_id,))
    conn.execute(
        "DELETE FROM chats WHERE peer_kind IN ('user', 'bot') AND peer_id = ?",
        (peer_user_id,),
    )


def _upsert_chat_meta(
    conn: sqlite3.Connection,
    peer_kind: str,
    peer_id: int,
    title: str,
    username: str,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO chats (peer_kind, peer_id, title, username, updated_utc)
        VALUES (?, ?, ?, ?, datetime('now'))
        """,
        (peer_kind, peer_id, title or "", username or ""),
    )


def _channel_peer_kind(ch: Channel) -> str:
    if getattr(ch, "megagroup", False):
        return "supergroup"
    if getattr(ch, "broadcast", False):
        return "channel"
    return "channel"


def _peer_needs_resync(conn: sqlite3.Connection, peer_user_id: int) -> bool:
    """True unless cache shows only our own messages (no incoming from peer)."""
    n = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE peer_user_id = ?",
        (peer_user_id,),
    ).fetchone()[0]
    if n == 0:
        return True
    has_null = conn.execute(
        "SELECT 1 FROM messages WHERE peer_user_id = ? AND from_me IS NULL LIMIT 1",
        (peer_user_id,),
    ).fetchone()
    if has_null:
        return True
    incoming = conn.execute(
        "SELECT 1 FROM messages WHERE peer_user_id = ? AND from_me = 0 LIMIT 1",
        (peer_user_id,),
    ).fetchone()
    return incoming is not None


def _like_substring(q: str) -> str:
    """Wildcard substring for SQL LIKE; query is normalized with casefold (Unicode-safe)."""
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc.casefold()}%"


def _sql_casefold(s: object) -> str:
    if s is None:
        return ""
    return str(s).casefold()


def search_local(
    db_path: Path,
    query: str,
    header: bool,
    *,
    verbose: bool = False,
) -> int:
    conn = _open_db(db_path)
    excl_deleted = "peer_user_id NOT IN (SELECT peer_user_id FROM deleted_peers)"
    cur = conn.execute(f"SELECT COUNT(*) FROM messages WHERE {excl_deleted}")
    total_rows = cur.fetchone()[0]
    if total_rows == 0:
        conn.close()
        raise SystemExit(
            "Cache is empty after refresh (no private user messages were stored)."
        )

    if not query.strip():
        conn.close()
        raise SystemExit("Search query must not be empty.")

    conn.create_function(
        "_tg_casefold", 1, _sql_casefold, deterministic=True
    )
    like_param = _like_substring(query)
    where = f"_tg_casefold(text) LIKE ? ESCAPE '\\' AND {excl_deleted}"
    base_params: list = [like_param]

    if verbose:
        sql = (
            "SELECT peer_user_id, display_name, message_id, date_utc, text "
            f"FROM messages WHERE {where} ORDER BY date_utc DESC, message_id DESC"
        )
        exec_params = list(base_params)
        out_header = ["peer_user_id", "display_name", "message_id", "date_utc", "text"]
    else:
        sql = (
            "SELECT peer_user_id, MAX(display_name) "
            f"FROM messages WHERE {where} GROUP BY peer_user_id "
            "ORDER BY MAX(date_utc) DESC, peer_user_id DESC"
        )
        exec_params = list(base_params)
        out_header = ["peer_user_id", "display_name"]

    n = 0
    if verbose:
        w = csv.writer(sys.stdout, delimiter="\t", lineterminator="\n")
        if header:
            w.writerow(out_header)
        for row in conn.execute(sql, exec_params):
            uid, disp, mid, dt, body = row
            w.writerow(
                [
                    uid,
                    _one_line(disp or ""),
                    mid,
                    dt or "",
                    _one_line(body or ""),
                ]
            )
            n += 1
    else:
        rows = [
            (int(r[0]), _one_line(r[1] or ""))
            for r in conn.execute(sql, exec_params)
        ]
        n = len(rows)
        w_id = max((len("peer_user_id"),) + tuple(len(str(u)) for u, _ in rows), default=len("peer_user_id"))
        w_name = max((len("display_name"),) + tuple(len(d) for _, d in rows), default=len("display_name"))
        if header:
            print(
                f"{'peer_user_id'.ljust(w_id)} {'display_name'.ljust(w_name)}",
                flush=True,
            )
        for uid, disp in rows:
            print(
                f"{str(uid).ljust(w_id)} {disp.ljust(w_name)}",
                flush=True,
            )
    conn.close()
    return n


def _row_tuple(peer: User, message: object) -> tuple:
    uid = peer.id
    name_src = peer
    sender = getattr(message, "sender", None)
    if isinstance(sender, User) and sender.id == uid:
        if len(utils.get_display_name(sender)) > len(utils.get_display_name(name_src)):
            name_src = sender
    uname = (name_src.username or "")
    disp = _private_chat_label(name_src)
    mid = int(getattr(message, "id", 0))
    dt = message.date.isoformat() if getattr(message, "date", None) else ""
    body = getattr(message, "message", None) or ""
    from_me = 1 if bool(getattr(message, "out", False)) else 0
    return (uid, mid, dt, uname, _one_line(disp), body, from_me)


class _TracePeerSummary:
    """One stderr line per DM when finished (works in any terminal / capture)."""

    def __init__(self, file) -> None:
        self._file = file
        self._peer_label = ""
        self._peer_new = 0
        self._total_new = 0

    def start_peer(self, peer: User) -> None:
        who = _private_chat_label(peer) or f"id={peer.id}"
        self._peer_label = f"{who} (id={peer.id})"[:80]
        self._peer_new = 0

    def note_messages(self, n: int) -> None:
        if n <= 0:
            return
        self._peer_new += n
        self._total_new += n

    def end_peer(self) -> None:
        print(
            f"# cache  {self._peer_label}  +{self._peer_new} new  "
            f"(+{self._total_new} this run)",
            file=self._file,
            flush=True,
        )


class _CacheTraceLive:
    """In-place stderr line (TTY). \\r only affects one screen line — keep line short, no wrap."""

    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    _LIVE_MAX = 72

    def __init__(self, file) -> None:
        self._file = file
        self._peer_label = ""
        self._peer_new = 0
        self._total_new = 0
        self._last_draw: float | None = None
        self._live_started = False

    def start_peer(self, peer: User) -> None:
        who = _private_chat_label(peer) or f"id={peer.id}"
        self._peer_label = f"{who} (id={peer.id})"
        self._peer_new = 0
        self._live_started = False

    def note_messages(self, n: int) -> None:
        if n <= 0:
            return
        self._peer_new += n
        self._total_new += n
        self._live_started = True
        self._draw()

    @staticmethod
    def _indeterminate_bar(t: float, width: int = 8) -> str:
        pos = int(t * 10) % (width + 2)
        cells = []
        for i in range(width):
            cells.append("#" if pos - 2 <= i <= pos else ".")
        return "[" + "".join(cells) + "]"

    def _draw(self) -> None:
        now = time.monotonic()
        if self._last_draw is not None and (now - self._last_draw) < 0.1:
            return
        self._last_draw = now
        frame = self._FRAMES[int(now * 12) % len(self._FRAMES)]
        bar = self._indeterminate_bar(now)
        peer = self._peer_label
        if len(peer) > 36:
            peer = peer[:33] + "…"
        line = f"{frame}{bar} {peer} +{self._peer_new} (+{self._total_new})"
        if len(line) > self._LIVE_MAX:
            line = line[: self._LIVE_MAX - 1] + "…"
        self._file.write("\r\033[K" + line)
        self._file.flush()

    def end_peer(self) -> None:
        if self._live_started:
            self._file.write("\r\033[K")
            self._file.flush()
        print(
            f"# cache  {self._peer_label}  +{self._peer_new} new  "
            f"(+{self._total_new} this run)",
            file=self._file,
            flush=True,
        )


async def refresh_cache(
    db_path: Path,
    skip_bots: bool,
    per_peer_limit: int | None,
    trace_ui: object | None,
    *,
    quiet: bool = False,
    recent_peer_limit: int | None = 20,
    rescan_top_all: bool = False,
) -> None:
    client = make_client()
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Not authorized. Run: .venv/bin/python -m telegram_toolkit auth")

    conn = _open_db(db_path)
    ins = """
        INSERT OR REPLACE INTO messages
        (peer_user_id, message_id, date_utc, username, display_name, text, from_me)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    n_dialogs = 0
    n_msgs = 0
    n_private_slot = 0
    n_deleted_skipped = 0
    n_meta_chats = 0
    selective = recent_peer_limit is not None and not rescan_top_all
    cap_channel_meta = recent_peer_limit is not None

    dialog_slot = 0
    async for dialog in client.iter_dialogs():
        slot = dialog_slot
        dialog_slot += 1
        ent = dialog.entity
        if isinstance(ent, Channel):
            if not cap_channel_meta or slot < recent_peer_limit:
                kind = _channel_peer_kind(ent)
                title = (ent.title or "").strip()
                uname = (ent.username or "").strip()
                _upsert_chat_meta(conn, kind, int(ent.id), title, uname)
                n_meta_chats += 1
            conn.commit()
            continue

        if isinstance(ent, Chat):
            if not cap_channel_meta or slot < recent_peer_limit:
                title = (ent.title or "").strip()
                _upsert_chat_meta(conn, "group", int(ent.id), title, "")
                n_meta_chats += 1
            conn.commit()
            continue

        if not isinstance(ent, User):
            continue

        peer = ent
        if peer.deleted:
            _purge_deleted_peer(conn, peer.id)
            conn.commit()
            n_deleted_skipped += 1
            continue

        label = _private_chat_label(peer) or ""
        uname = (peer.username or "").strip()
        kind = "bot" if peer.bot else "user"
        _upsert_chat_meta(conn, kind, int(peer.id), label, uname)
        n_meta_chats += 1

        if skip_bots and peer.bot:
            conn.commit()
            continue

        if recent_peer_limit is not None and n_private_slot >= recent_peer_limit:
            conn.commit()
            continue

        n_private_slot += 1
        uid = peer.id

        if selective and not _peer_needs_resync(conn, uid):
            conn.commit()
            continue

        try:
            peer = await client.get_entity(peer)
        except Exception:
            pass
        if not isinstance(peer, User):
            conn.commit()
            continue
        if peer.deleted:
            _purge_deleted_peer(conn, peer.id)
            conn.commit()
            n_deleted_skipped += 1
            n_private_slot -= 1
            continue

        n_dialogs += 1
        row = conn.execute(
            "SELECT MAX(message_id) FROM messages WHERE peer_user_id = ?",
            (uid,),
        ).fetchone()
        cached_max: int | None = int(row[0]) if row[0] is not None else None

        if trace_ui:
            trace_ui.start_peer(peer)

        iter_kw: dict = {"offset_id": 0}
        if cached_max is None and per_peer_limit is not None:
            iter_kw["limit"] = per_peer_limit

        batch: list[tuple] = []
        async for msg in client.iter_messages(peer, **iter_kw):
            mid = int(getattr(msg, "id", 0) or 0)
            if cached_max is not None and mid <= cached_max:
                break
            batch.append(_row_tuple(peer, msg))
            if trace_ui:
                trace_ui.note_messages(1)
            if len(batch) >= 500:
                conn.executemany(ins, batch)
                n_msgs += len(batch)
                batch.clear()
        if batch:
            conn.executemany(ins, batch)
            n_msgs += len(batch)
            batch.clear()

        conn.commit()
        if trace_ui:
            trace_ui.end_peer()
        elif not quiet and n_dialogs % 20 == 0:
            print(f"# synced_dialogs={n_dialogs} messages_written={n_msgs}", file=sys.stderr)

    conn.close()
    await client.disconnect()
    if not quiet:
        if n_deleted_skipped:
            print(
                f"# cache  skipped {n_deleted_skipped} deleted-account peer(s) "
                "(removed from cache; excluded from search)",
                file=sys.stderr,
            )
        print(
            f"# done meta_chats={n_meta_chats} dm_dialogs_synced={n_dialogs} "
            f"messages_touched={n_msgs}",
            file=sys.stderr,
        )


async def show_peer(user_id: int, db_path: Path, *, quiet: bool) -> None:
    """Print cache + Telegram profile for one private-chat user id."""
    n, disp_max, user_max = 0, "", ""
    if db_path.is_file():
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*), MAX(display_name), MAX(username) FROM messages WHERE peer_user_id = ?",
                (user_id,),
            ).fetchone()
            n, disp_max, user_max = int(row[0] or 0), row[1] or "", row[2] or ""
        except sqlite3.Error as e:
            print(f"(cache read error: {e})", file=sys.stderr)
        finally:
            conn.close()

    if not quiet:
        print(f"# show peer_user_id={user_id} cache={db_path}", file=sys.stderr)

    print("--- cache (messages table) ---")
    if not db_path.is_file():
        print(f"(no database file at {db_path})")
    elif n == 0:
        print("(no rows for this peer_user_id)")
    else:
        print(f"cached_messages\t{n}")
        print(f"display_name\t{disp_max or '(empty)'}")
        print(f"username\t{user_max or '(empty)'}")

    print("--- telegram (get_entity) ---")
    client = make_client()
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise SystemExit("Not authorized. Run: .venv/bin/python -m telegram_toolkit auth")
    try:
        ent = await client.get_entity(user_id)
    except Exception as e:
        await client.disconnect()
        print(f"(failed: {e})")
        print(
            "Hint: id must be a user you can resolve (e.g. in dialogs, or known @username).",
            file=sys.stderr,
        )
        return
    await client.disconnect()

    if not isinstance(ent, User):
        print(f"(entity is {type(ent).__name__}, not a User)")
        return

    u = ent
    label = _private_chat_label(u)
    lines = [
        ("user_id", str(u.id)),
        ("chat_list_name", label or "(empty)"),
        ("first_name", u.first_name or ""),
        ("last_name", u.last_name or ""),
        ("username", f"@{u.username}" if u.username else ""),
        ("phone", (u.phone or "").strip()),
        ("bot", str(bool(u.bot))),
        ("deleted", str(bool(u.deleted))),
        ("verified", str(bool(u.verified))),
        ("restricted", str(bool(u.restricted))),
    ]
    for k, v in lines:
        if v == "" and k in ("first_name", "last_name", "username", "phone"):
            continue
        print(f"{k}\t{v}")
    if u.username:
        print(f"https://t.me/{u.username}")
