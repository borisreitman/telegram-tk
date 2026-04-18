#!/usr/bin/env python3
"""
Find dialogs by **title / display name** using phonetic-style matching.

Matches **users**, **bots**, **channels**, **supergroups**, and **basic groups** from the
database (filled on **rescan** / **full-rescan**), plus any user rows
backfilled from 1:1 messages. Russian (Cyrillic) is transliterated (``cyrtranslit``);
each **word** is scored with ``rapidfuzz.WRatio``.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sqlite3
import sys
from pathlib import Path

import cyrtranslit
from rapidfuzz import fuzz
from telethon import utils as tg_utils

from telegram_toolkit.dm_cache import DEFAULT_CACHE, _open_db

_CYRILLIC_RE = re.compile(r"[\u0400-\u052f]")

# Channel-like peers ``list`` may resolve from the same name database as ``name``.
LISTABLE_PEER_KINDS = frozenset({"channel", "supergroup", "group"})


def _to_match_space(text: str) -> str:
    """Lowercase Latin-ish string: Russian → transliterated, punctuation stripped."""
    s = (text or "").strip()
    if not s:
        return ""
    if _CYRILLIC_RE.search(s):
        try:
            s = cyrtranslit.to_latin(s, "ru")
        except Exception:
            pass
    s = s.casefold()
    out: list[str] = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
        elif ch.isspace() or ch in "'-":
            out.append(" ")
        else:
            out.append(" ")
    return " ".join("".join(out).split())


def _hay_words(hay: str) -> list[str]:
    return [w for w in hay.split() if w]


def _needle_tokens(needle: str) -> list[str]:
    return [t for t in needle.split() if t]


def _prefix_word_match(nt: str, w: str) -> bool:
    """Typed query is a prefix of a display word."""
    if len(nt) < 3 or len(w) < 3:
        return False
    if len(nt) > len(w):
        return False
    if not w.startswith(nt):
        return False
    return (len(w) - len(nt)) <= max(3, len(nt) // 2 + 1)


def _word_matches(nt: str, w: str, *, min_score: int) -> bool:
    if not nt or not w:
        return False
    if nt == w:
        return True
    if len(nt) >= 4 and len(w) < 3:
        return False
    if _prefix_word_match(nt, w):
        return True
    if len(nt) >= 4 and nt in w:
        return True
    if len(w) >= 4 and w in nt:
        return True
    if len(w) < len(nt):
        return False
    return int(fuzz.WRatio(nt, w)) >= min_score


def display_name_matches_query(
    display_name: str,
    query: str,
    *,
    min_score: int,
) -> bool:
    needle = _to_match_space(query)
    hay = _to_match_space(display_name)
    if not needle or not hay:
        return False
    words = _hay_words(hay)
    if not words:
        return False
    tokens = _needle_tokens(needle)
    if not tokens:
        return False
    for nt in tokens:
        if len(nt) == 1:
            if not any(nt == w for w in words):
                return False
            continue
        if not any(_word_matches(nt, w, min_score=min_score) for w in words):
            return False
    return True


def _rank_hit(display_name: str, query: str, *, min_score: int) -> int:
    """Sort key: higher = stronger match among rows that already passed the filter."""
    needle = _to_match_space(query)
    hay = _to_match_space(display_name)
    words = _hay_words(hay)
    best = 0
    for nt in _needle_tokens(needle):
        if len(nt) <= 1:
            best = max(best, 55)
            continue
        for w in words:
            if nt == w:
                best = max(best, 100)
            elif len(nt) >= 4 and nt in w:
                best = max(best, 96)
            elif _prefix_word_match(nt, w):
                best = max(best, 93)
            elif int(fuzz.WRatio(nt, w)) >= min_score:
                best = max(best, int(fuzz.WRatio(nt, w)))
    return best


def _safe_cell(s: str) -> str:
    return (s or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")


def _print_name_results(hits: list[tuple[str, int, str, str]]) -> None:
    """Space-padded columns."""
    rows = [(k, str(p), _safe_cell(t), _safe_cell(u)) for k, p, t, u in hits]
    w_kind = max((len("peer_kind"),) + tuple(len(r[0]) for r in rows), default=len("peer_kind"))
    w_id = max((len("peer_id"),) + tuple(len(r[1]) for r in rows), default=len("peer_id"))
    w_title = max((len("title"),) + tuple(len(r[2]) for r in rows), default=len("title"))
    w_user = max((len("username"),) + tuple(len(r[3]) for r in rows), default=len("username"))
    for kind, pid_s, tit, un in rows:
        print(
            f"{kind.ljust(w_kind)} {pid_s.ljust(w_id)} {tit.ljust(w_title)} {un.ljust(w_user)}",
            flush=True,
        )


def _label_for_match(title: str, username: str) -> str:
    t = (title or "").strip()
    u = (username or "").strip()
    if u and not u.startswith("@"):
        u = f"@{u}"
    if t and u:
        return f"{t} {u}"
    return t or u


def _cache_chat_rows(conn: sqlite3.Connection, *, channel_id: int | None = None) -> list[tuple[str, int, str, str]]:
    """Rows: peer_kind, peer_id, title, username (username without @ in DB)."""
    sql = """
        SELECT c.peer_kind, c.peer_id, COALESCE(c.title, ''), COALESCE(c.username, '')
        FROM chats c
        WHERE NOT (
            c.peer_kind IN ('user', 'bot')
            AND c.peer_id IN (SELECT peer_user_id FROM deleted_peers)
        )
    """
    params = []
    if channel_id is not None:
        sql += " AND c.peer_id IN (SELECT user_id FROM channel_member_snapshots WHERE channel_id = ?)"
        params.append(channel_id)

    cur = conn.execute(sql, params)
    return [(str(r[0]), int(r[1]), (r[2] or "").strip(), (r[3] or "").strip()) for r in cur.fetchall()]


def name_lookup_hits(
    rows: list[tuple[str, int, str, str]],
    query: str,
    *,
    min_score: int,
) -> list[tuple[str, int, str, str]]:
    """Score and sort rows."""
    q = query.strip()
    if not q:
        return []
    hits: list[tuple[str, int, str, str]] = []
    for kind, pid, title, uname in rows:
        lab = _label_for_match(title, uname)
        # Check title/name label
        if display_name_matches_query(lab, q, min_score=min_score):
            hits.append((kind, pid, title, uname))
        # Also check username explicitly if not already matched
        elif uname and q.lower().lstrip("@") == uname.lower():
            hits.append((kind, pid, title, uname))

    hits.sort(
        key=lambda t: (
            -_rank_hit(_label_for_match(t[2], t[3]), q, min_score=min_score),
            t[0],
            t[1],
        ),
    )
    return hits


def name_search_hits(
    db_path: Path,
    query: str,
    *,
    min_score: int,
    channel_id: int | None = None,
) -> list[tuple[str, int, str, str]]:
    """SQLite **chats** name search."""
    if not db_path.is_file():
        return []
    conn = _open_db(db_path)
    try:
        rows = _cache_chat_rows(conn, channel_id=channel_id)
    finally:
        conn.close()
    return name_lookup_hits(rows, query, min_score=min_score)


def parse_peer_id_literal_for_chats_lookup(literal: str) -> int | None:
    """If ``literal`` is a numeric channel/group id form, return ``chats.peer_id``."""
    from telethon import utils as tg_utils
    from telethon.tl.types import PeerChannel, PeerChat

    s = literal.strip().replace(" ", "").replace("_", "")
    if not s:
        return None
    if s.startswith("+") and len(s) > 1:
        s = s[1:]
    if s.startswith("-100") and len(s) > 4 and s[4:].isdigit():
        return int(s[4:])
    if s.startswith("-") and s[1:].isdigit():
        inner, peer_type = tg_utils.resolve_id(int(s))
        if peer_type is PeerChannel:
            return inner
        if peer_type is PeerChat:
            return inner
        return None
    if s.isdigit():
        return int(s)
    return None


def fetch_listable_chat_row_by_peer_id(
    db_path: Path,
    peer_id: int,
) -> tuple[str, int, str, str] | None:
    """Return the single listable ``chats`` row for ``peer_id``."""
    if not db_path.is_file():
        return None
    placeholders = ",".join("?" * len(LISTABLE_PEER_KINDS))
    kinds_tuple = tuple(LISTABLE_PEER_KINDS)
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT peer_kind, peer_id, COALESCE(title, ''), COALESCE(username, '')
            FROM chats
            WHERE peer_id = ? AND peer_kind IN ({placeholders})
            """,
            (peer_id,) + kinds_tuple,
        ).fetchall()
    finally:
        conn.close()
    if len(rows) != 1:
        return None
    r = rows[0]
    return (str(r[0]), int(r[1]), (r[2] or "").strip(), (r[3] or "").strip())


def fetch_listable_chat_row_by_username(
    db_path: Path,
    username: str,
) -> tuple[str, int, str, str] | None:
    """Return the single listable ``chats`` row for an exact username match."""
    if not db_path.is_file():
        return None
    u = username.strip().lstrip("@")
    if not u:
        return None
    placeholders = ",".join("?" * len(LISTABLE_PEER_KINDS))
    kinds_tuple = tuple(LISTABLE_PEER_KINDS)
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT peer_kind, peer_id, COALESCE(title, ''), COALESCE(username, '')
            FROM chats
            WHERE username = ? COLLATE NOCASE AND peer_kind IN ({placeholders})
            """,
            (u,) + kinds_tuple,
        ).fetchall()
    finally:
        conn.close()
    if len(rows) != 1:
        return None
    r = rows[0]
    return (str(r[0]), int(r[1]), (r[2] or "").strip(), (r[3] or "").strip())


def fetch_chat_rows_by_peer_id(db_path: Path, peer_id: int) -> list[tuple[str, int, str, str]]:
    """Return all ``chats`` rows with this ``peer_id``."""
    if not db_path.is_file():
        return []
    conn = _open_db(db_path)
    try:
        cur = conn.execute(
            """
            SELECT peer_kind, peer_id, COALESCE(title, ''), COALESCE(username, '')
            FROM chats
            WHERE peer_id = ?
            ORDER BY peer_kind, peer_id
            """,
            (peer_id,),
        )
        return [(str(r[0]), int(r[1]), (r[2] or "").strip(), (r[3] or "").strip()) for r in cur.fetchall()]
    finally:
        conn.close()


async def find_in_cache(
    db_path: Path,
    query: str,
    *,
    min_score: int,
    channel: str | None = None,
    pick: int | None = None,
) -> int:
    if not db_path.is_file():
        raise SystemExit(f"error: no database at {db_path}. Run 'rescan' first.")
    db = db_path.resolve()

    channel_id: int | None = None
    if channel:
        from telegram_toolkit.client import make_client
        from telegram_toolkit.resolver import resolve_listable_entity

        client = make_client()
        await client.connect()
        try:
            entity = await resolve_listable_entity(
                client,
                channel,
                cache_path=db_path,
                name_min_score=min_score,
                pick=pick,
            )
            channel_id = tg_utils.get_peer_id(entity)
        finally:
            await client.disconnect()

    parsed_id = parse_peer_id_literal_for_chats_lookup(query)
    if parsed_id is not None:
        id_hits = fetch_chat_rows_by_peer_id(db, parsed_id)
        if channel_id is not None:
            conn = _open_db(db)
            try:
                member_uids = {r[0] for r in conn.execute("SELECT user_id FROM channel_member_snapshots WHERE channel_id = ?", (channel_id,)).fetchall()}
                id_hits = [h for h in id_hits if h[1] in member_uids]
            finally:
                conn.close()

        if id_hits:
            _print_name_results(id_hits)
            return len(id_hits)

    hits = name_search_hits(db, query, min_score=min_score, channel_id=channel_id)
    _print_name_results(hits)
    return len(hits)


async def run_find_dm_peer(
    query: str,
    *,
    cache: Path,
    min_score: int,
    channel: str | None = None,
    pick: int | None = None,
) -> int:
    if not (1 <= min_score <= 100):
        raise SystemExit("error: --min-score must be between 1 and 100")
    q = query.strip()
    if not q:
        raise SystemExit("error: pass a non-empty name query")
    return await find_in_cache(cache.resolve(), q, min_score=min_score, channel=channel, pick=pick)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Find chats by title / name, username or peer id (phonetic / fuzzy).",
    )
    p.add_argument(
        "name",
        nargs="+",
        metavar="TEXT",
        help="Name fragment, username, or numeric ID.",
    )
    p.add_argument(
        "--min-score",
        type=int,
        default=82,
        metavar="N",
        help="rapidfuzz WRatio threshold per word (1–100; default: 82)",
    )
    p.add_argument(
        "--channel",
        metavar="CHANNEL",
        help="Limit search to members of this channel (requires cached members)",
    )
    p.add_argument(
        "--pick",
        type=int,
        default=None,
        metavar="N",
        help="When several fuzzy matches for --channel: use the Nth row (1-based) without prompting",
    )
    args = p.parse_args()
    q = " ".join(args.name).strip()
    asyncio.run(
        run_find_dm_peer(
            q,
            cache=DEFAULT_CACHE,
            min_score=args.min_score,
            channel=args.channel,
            pick=args.pick,
        )
    )


if __name__ == "__main__":
    main()
