#!/usr/bin/env python3
"""
Find dialogs by **title / display name** using phonetic-style matching.

Matches **users**, **bots**, **channels**, **supergroups**, and **basic groups** from the
**chats** SQLite table (filled on **rescan** / **full-rescan**), plus any user rows
backfilled from cached 1:1 messages. Russian (Cyrillic) is transliterated (``cyrtranslit``);
each **word** is scored with ``rapidfuzz.WRatio``. Search is **cache-only** (SQLite); neither
``name`` nor ``list`` fuzzy fallback scans live Telegram dialogs.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

import cyrtranslit
from rapidfuzz import fuzz
from telegram_toolkit.dm_cache import DEFAULT_CACHE, _open_db

_CYRILLIC_RE = re.compile(r"[\u0400-\u052f]")

# Channel-like peers ``list`` may resolve from the same name cache as ``telegram-tk name``.
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
    """Typed query is a prefix of a display word (``mak`` / ``maksim``, ``maks`` / ``maksim``)."""
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


def _print_name_results(hits: list[tuple[str, int, str, str]], *, header: bool) -> None:
    """Space-padded columns (same idea as ``search`` default stdout)."""
    rows = [(k, str(p), _safe_cell(t), _safe_cell(u)) for k, p, t, u in hits]
    w_kind = max((len("peer_kind"),) + tuple(len(r[0]) for r in rows), default=len("peer_kind"))
    w_id = max((len("peer_id"),) + tuple(len(r[1]) for r in rows), default=len("peer_id"))
    w_title = max((len("title"),) + tuple(len(r[2]) for r in rows), default=len("title"))
    w_user = max((len("username"),) + tuple(len(r[3]) for r in rows), default=len("username"))
    if header:
        print(
            f"{'peer_kind'.ljust(w_kind)} {'peer_id'.ljust(w_id)} "
            f"{'title'.ljust(w_title)} {'username'.ljust(w_user)}",
            flush=True,
        )
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
    """Score and sort rows the same way as ``telegram-tk name`` (single implementation)."""
    q = query.strip()
    if not q:
        return []
    hits: list[tuple[str, int, str, str]] = []
    for kind, pid, title, uname in rows:
        lab = _label_for_match(title, uname)
        if display_name_matches_query(lab, q, min_score=min_score):
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
    """
    SQLite **chats** name search: same hit list as ``telegram-tk name`` / ``find_in_cache``,
    without printing. Returns ``[]`` if ``db_path`` is missing (``name`` CLI raises instead).
    """
    if not db_path.is_file():
        return []
    conn = _open_db(db_path)
    try:
        rows = _cache_chat_rows(conn, channel_id=channel_id)
    finally:
        conn.close()
    return name_lookup_hits(rows, query, min_score=min_score)


def resolve_channel_id_from_cache(db_path: Path, identifier: str) -> int | None:
    """Resolve channel/group ID from cache identifier (@username, ID, or title fragment)."""
    if not db_path.is_file():
        return None
    parsed_id = parse_peer_id_literal_for_chats_lookup(identifier)
    row: tuple[str, int, str, str] | None = None
    if parsed_id is not None:
        # Check if it's a known channel/group
        row = fetch_listable_chat_row_by_peer_id(db_path, parsed_id)

    if row is None:
        # Try name search for channel/group
        hits = name_search_hits(db_path, identifier, min_score=82)
        listable = [h for h in hits if h[0] in LISTABLE_PEER_KINDS]
        if listable:
            row = listable[0]

    if row:
        kind, pid, _title, _uname = row
        if kind == "group":
            return -pid
        if kind in ("channel", "supergroup"):
            return -(1000000000000 + pid)
    return None


def parse_peer_id_literal_for_chats_lookup(literal: str) -> int | None:
    """
    If ``literal`` is a numeric channel/group id form, return ``chats.peer_id`` for lookup.

    Accepts a plain positive **channel id**, ``-100…`` Bot API style, or a negative **marked**
    id that ``telethon.utils.resolve_id`` classifies as ``PeerChannel`` / ``PeerChat``.
    """
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
    """Return the single listable ``chats`` row for ``peer_id``, or ``None`` if none / ambiguous."""
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


def fetch_chat_rows_by_peer_id(db_path: Path, peer_id: int) -> list[tuple[str, int, str, str]]:
    """Return all ``chats`` rows with this ``peer_id`` (any ``peer_kind``), sorted for stable output."""
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


def find_in_cache(
    db_path: Path,
    query: str,
    *,
    header: bool,
    min_score: int,
    channel: str | None = None,
) -> int:
    if not db_path.is_file():
        raise SystemExit(f"name: no cache database at {db_path}")
    db = db_path.resolve()

    channel_id: int | None = None
    if channel:
        channel_id = resolve_channel_id_from_cache(db, channel)
        if channel_id is None:
            raise SystemExit(f"name: could not resolve channel {channel!r} from cache. Run 'telegram-tk list' first to cache members.")

    parsed_id = parse_peer_id_literal_for_chats_lookup(query)
    if parsed_id is not None:
        id_hits = fetch_chat_rows_by_peer_id(db, parsed_id)
        if channel_id is not None:
            # Filter id_hits by membership if channel is specified
            conn = _open_db(db)
            try:
                # We reuse _cache_chat_rows logic by manually filtering or just checking membership
                member_uids = {r[0] for r in conn.execute("SELECT user_id FROM channel_member_snapshots WHERE channel_id = ?", (channel_id,)).fetchall()}
                id_hits = [h for h in id_hits if h[1] in member_uids]
            finally:
                conn.close()

        if id_hits:
            _print_name_results(id_hits, header=header)
            return len(id_hits)

    hits = name_search_hits(db, query, min_score=min_score, channel_id=channel_id)
    _print_name_results(hits, header=header)
    return len(hits)


def run_find_dm_peer(
    query: str,
    *,
    cache: Path,
    header: bool,
    min_score: int,
    channel: str | None = None,
) -> int:
    if not (1 <= min_score <= 100):
        raise SystemExit("name: --min-score must be between 1 and 100")
    q = query.strip()
    if not q:
        raise SystemExit("name: pass a non-empty name query")
    return find_in_cache(cache.resolve(), q, header=header, min_score=min_score, channel=channel)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Find chats by title / name or peer id (users, channels, groups; phonetic / fuzzy).",
    )
    p.add_argument(
        "name",
        nargs="+",
        metavar="TEXT",
        help="Name fragment, or numeric / -100… / marked id (shows peer_id and title from cache)",
    )
    p.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE,
        help=f"SQLite path (default: {DEFAULT_CACHE})",
    )
    p.add_argument(
        "--min-score",
        type=int,
        default=82,
        metavar="N",
        help="rapidfuzz WRatio threshold per word (1–100; default: 82)",
    )
    p.add_argument(
        "--header",
        action="store_true",
        help="Print TSV header row",
    )
    args = p.parse_args()
    q = " ".join(args.name).strip()
    run_find_dm_peer(
        q,
        cache=args.cache,
        header=args.header,
        min_score=args.min_score,
    )


if __name__ == "__main__":
    main()
