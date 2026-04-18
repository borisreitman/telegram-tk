#!/usr/bin/env python3
"""
List channel / supergroup members as TSV on stdout (grep-friendly), or as CSV
when ``--output`` is set.

Rows are sorted by **join date** (from Telethon ``user.participant.date`` when present),
then by ``user_id``. ``ChannelParticipantCreator`` / ``ChatParticipantCreator`` have no
join date and sort first. Rows without a usable date sort last.

Columns: user_id, username, first_name, last_name, joined_date, joined_time,
last_private_date, last_private_time (same output time zone, default US Pacific ``America/Los_Angeles``;
empty when join time or private cache is unknown). Use ``--tz`` for an IANA zone (``UTC``, ``Europe/Berlin``, …).

**Resolving CHANNEL**: (1) numeric **peer id** — ``chats`` exact match, else ``get_entity``
on ``-100…`` / ``-id`` forms; (2) otherwise **same** SQLite fuzzy search as
``telegram-tk name`` (``name_search_hits``), keeping only **channel** / **supergroup** /
**group** hits. **Listing members** uses ``iter_participants`` (Telegram);
``--max-cache-age`` / ``--refresh`` only affect optional local **member** snapshots.

One listable name match resolves automatically; several require ``--pick N`` or a TTY prompt.

Example:
  set -a && source .env && set +a
  telegram-tk list @mychannel
  telegram-tk list -1001234567890
  telegram-tk list @mychannel --refresh
  telegram-tk list @mychannel --output members.csv
  .venv/bin/python -m telegram_toolkit.list_users @mychannel | grep -i pattern
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sqlite3
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telethon import utils
from telethon.errors.rpcerrorlist import ChatAdminRequiredError
from telethon.tl.types import User

from telegram_toolkit.client import make_client
from telegram_toolkit.dm_cache import DEFAULT_CACHE, _open_db
from telegram_toolkit.find_dm_peer import (
    LISTABLE_PEER_KINDS,
    _label_for_match,
    fetch_listable_chat_row_by_peer_id,
    name_search_hits,
    parse_peer_id_literal_for_chats_lookup,
)

# US Pacific (PST/PDT via DST rules). IANA name is the portable default.
DEFAULT_LIST_OUTPUT_TZ = "America/Los_Angeles"


def _normalize_tz_name(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return DEFAULT_LIST_OUTPUT_TZ
    u = s.upper().replace(" ", "_")
    if u in ("PST", "PDT", "PT"):
        return "America/Los_Angeles"
    return s.strip()


def _resolve_output_tz(name: str) -> ZoneInfo:
    key = _normalize_tz_name(name)
    try:
        return ZoneInfo(key)
    except ZoneInfoNotFoundError:
        raise SystemExit(
            f"list: unknown time zone {name!r} (resolved {key!r}). "
            "Use an IANA name, e.g. America/Los_Angeles, UTC, Europe/London. "
            "Shorthand accepted: PST, PDT, PT → America/Los_Angeles."
        ) from None


def _parse_stored_iso_to_utc(iso: str) -> datetime | None:
    """Parse ISO strings from Telethon / SQLite into aware UTC, or ``None`` if missing / invalid."""
    s = (iso or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_iso_local_date_time(iso: str, tz: ZoneInfo) -> tuple[str, str]:
    """Date and time strings in ``tz`` for a stored UTC instant, or empty pair."""
    dt = _parse_stored_iso_to_utc(iso)
    if dt is None:
        return ("", "")
    local = dt.astimezone(tz)
    return (local.strftime("%Y-%m-%d"), local.strftime("%H:%M:%S"))


def _participant_join(user: User) -> tuple[datetime, str]:
    """Sort key (aware UTC) and ISO ``joined_utc`` cell (empty if unknown / creator)."""
    part = getattr(user, "participant", None)
    if part is None:
        return (datetime.max.replace(tzinfo=timezone.utc), "")
    cls = part.__class__.__name__
    if cls in ("ChannelParticipantCreator", "ChatParticipantCreator"):
        return (datetime.min.replace(tzinfo=timezone.utc), "")
    raw = getattr(part, "date", None)
    if raw is None:
        return (datetime.max.replace(tzinfo=timezone.utc), "")
    if raw.tzinfo is None:
        raw = raw.replace(tzinfo=timezone.utc)
    else:
        raw = raw.astimezone(timezone.utc)
    return (raw, raw.isoformat())


def _sort_bucket(jd: datetime) -> int:
    zmin = datetime.min.replace(tzinfo=timezone.utc)
    zmax = datetime.max.replace(tzinfo=timezone.utc)
    if jd == zmin:
        return 0
    if jd == zmax:
        return 2
    return 1


def _fetch_max_private_dm_iso_by_peer(conn: sqlite3.Connection) -> dict[int, str]:
    """
    For each ``peer_user_id`` in ``messages``, the newest ``date_utc`` (ISO) among non-deleted peers.
    Used to correlate channel members with the local 1:1 message cache from **rescan** / **full-rescan**.
    """
    cur = conn.execute(
        """
        SELECT m.peer_user_id, MAX(m.date_utc)
        FROM messages m
        WHERE m.peer_user_id NOT IN (SELECT peer_user_id FROM deleted_peers)
        GROUP BY m.peer_user_id
        """
    )
    out: dict[int, str] = {}
    for uid, dt in cur.fetchall():
        s = (dt or "").strip()
        if s:
            out[int(uid)] = s
    return out


def _meta_fresh(fetched_at_utc: str, max_age_sec: int) -> bool:
    try:
        t = datetime.fromisoformat(fetched_at_utc.replace("Z", "+00:00"))
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - t.astimezone(timezone.utc)).total_seconds()
    return age >= 0 and age <= max_age_sec


def _load_snapshot_rows(conn: sqlite3.Connection, channel_id: int) -> list[tuple[int, str, str, str, str, int]]:
    cur = conn.execute(
        """
        SELECT user_id, username, first_name, last_name, joined_utc, sort_bucket
        FROM channel_member_snapshots
        WHERE channel_id = ?
        ORDER BY sort_bucket, joined_utc, user_id
        """,
        (channel_id,),
    )
    return list(cur)


def _replace_snapshot(
    conn: sqlite3.Connection,
    channel_id: int,
    rows: list[tuple[int, str, str, str, str, int]],
) -> None:
    conn.execute("DELETE FROM channel_member_snapshots WHERE channel_id = ?", (channel_id,))
    conn.executemany(
        """
        INSERT INTO channel_member_snapshots (
            channel_id, user_id, username, first_name, last_name, sort_bucket, joined_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (channel_id, uid, un, fn, ln, b, joined)
            for (uid, un, fn, ln, joined, b) in rows
        ],
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO channel_member_snapshot_meta (channel_id, fetched_at_utc, row_count)
        VALUES (?, ?, ?)
        """,
        (channel_id, datetime.now(timezone.utc).isoformat(), len(rows)),
    )


_MEMBER_COLUMNS = [
    "user_id",
    "username",
    "first_name",
    "last_name",
    "joined_date",
    "joined_time",
    "last_private_date",
    "last_private_time",
]


def _write_member_rows(
    rows: list[tuple[int, str, str, str, str]],
    limit: int | None,
    header: bool,
    *,
    output: Path | None,
    display_tz: ZoneInfo,
    last_private_iso_by_user: Mapping[int, str] | None = None,
) -> int:
    """Write member rows to stdout (TSV) or to ``output`` (UTF-8 CSV). Returns data row count."""
    dm_map = last_private_iso_by_user or {}
    if limit is not None:
        rows = rows[:limit]
    n = 0
    if output is None:
        w = csv.writer(sys.stdout, delimiter="\t", lineterminator="\n")
        if header:
            w.writerow(_MEMBER_COLUMNS)
        for uid, un, fn, ln, joined in rows:
            jd, jt = _format_iso_local_date_time(joined, display_tz)
            lp_d, lp_t = _format_iso_local_date_time(dm_map.get(int(uid), ""), display_tz)
            w.writerow([uid, un, fn, ln, jd, jt, lp_d, lp_t])
            n += 1
            if n % 5000 == 0:
                sys.stdout.flush()
        sys.stdout.flush()
        return n
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if header:
            w.writerow(_MEMBER_COLUMNS)
        for uid, un, fn, ln, joined in rows:
            jd, jt = _format_iso_local_date_time(joined, display_tz)
            lp_d, lp_t = _format_iso_local_date_time(dm_map.get(int(uid), ""), display_tz)
            w.writerow([uid, un, fn, ln, jd, jt, lp_d, lp_t])
            n += 1
    return n


def _marked_entity_ref_for_list_row(kind: str, peer_id: int, username: str) -> str | int:
    u = (username or "").strip()
    if u:
        return u if u.startswith("@") else f"@{u}"
    if kind == "group":
        return -peer_id
    if kind in ("channel", "supergroup"):
        return -(1000000000000 + peer_id)
    raise AssertionError(f"unexpected peer_kind {kind!r}")


def _select_listable_peer(
    hits: list[tuple[str, int, str, str]],
    *,
    query: str,
    pick: int | None,
) -> tuple[str, int, str, str]:
    if not hits:
        raise SystemExit(
            f"list: no channel/supergroup/group in name hits for {query!r} "
            "(same filter as ``telegram-tk name`` on ``name_search_hits``, then listable kinds). "
            f"Run ``telegram-tk name {query!r}`` to see rows; ``telegram-tk rescan`` if the chat is missing."
        )
    if len(hits) == 1:
        return hits[0]
    if pick is not None:
        if 1 <= pick <= len(hits):
            return hits[pick - 1]
        raise SystemExit(f"list: --pick must be between 1 and {len(hits)} (got {pick})")
    if not sys.stdin.isatty():
        lines = "\n".join(
            f"  {i}. {_label_for_match(t[2], t[3])}  ({t[0]} id={t[1]})"
            for i, t in enumerate(hits, 1)
        )
        raise SystemExit(
            f"list: ambiguous name {query!r} ({len(hits)} matches). "
            "Use --pick N, pass @username / id, or run in a terminal to choose interactively.\n"
            f"{lines}"
        )
    print(f"# list: no direct match for {query!r}; choose a channel/group:", file=sys.stderr)
    for i, t in enumerate(hits, 1):
        print(f"  {i}. {_label_for_match(t[2], t[3])}  ({t[0]} id={t[1]})", file=sys.stderr)
    while True:
        raw = input(f"Select [1-{len(hits)}]: ").strip()
        if not raw.isdigit():
            print("Enter a positive number.", file=sys.stderr)
            continue
        n = int(raw)
        if 1 <= n <= len(hits):
            return hits[n - 1]
        print(f"Enter a number from 1 to {len(hits)}.", file=sys.stderr)


async def _resolve_entity_for_list(
    client,
    channel: str,
    *,
    cache_path: Path,
    name_min_score: int,
    pick: int | None,
):
    raw = channel.strip()
    if not raw:
        raise SystemExit("list: CHANNEL must be non-empty")
    resolved_cache = cache_path.resolve()
    if not resolved_cache.is_file():
        raise SystemExit(
            f"list: no SQLite cache at {resolved_cache} (``telegram-tk name`` needs it too). "
            "Run: telegram-tk rescan"
        )

    row: tuple[str, int, str, str] | None = None
    parsed_id = parse_peer_id_literal_for_chats_lookup(raw)
    if parsed_id is not None:
        row = fetch_listable_chat_row_by_peer_id(resolved_cache, parsed_id)

    if row is None:
        all_hits = name_search_hits(resolved_cache, raw, min_score=name_min_score)
        listable = [h for h in all_hits if h[0] in LISTABLE_PEER_KINDS]
        if listable:
            row = _select_listable_peer(listable, query=raw, pick=pick)

    if row is None and parsed_id is not None:
        last_err: BaseException | None = None
        for ref in (-(1000000000000 + parsed_id), -parsed_id):
            try:
                ent = await client.get_entity(ref)
                if sys.stderr.isatty():
                    print(f"# list: resolved id {raw!r} via get_entity({ref})", file=sys.stderr)
                return ent
            except BaseException as e:
                if isinstance(e, (asyncio.CancelledError, KeyboardInterrupt)):
                    raise
                last_err = e
        raise SystemExit(
            f"list: id {raw!r} not in ``chats`` as channel/group and Telegram get_entity failed: {last_err}"
        ) from last_err

    if row is None:
        raise SystemExit(
            f"list: no listable channel/group for {raw!r} (try ``telegram-tk rescan``, "
            f"or ``telegram-tk name`` for title search). "
            f"Numeric ids: plain channel id, ``-100…``, or negative marked id."
        )

    ref = _marked_entity_ref_for_list_row(row[0], row[1], row[3])
    try:
        resolved = await client.get_entity(ref)
    except BaseException as e:
        if isinstance(e, (asyncio.CancelledError, KeyboardInterrupt)):
            raise
        raise SystemExit(
            f"list: cache row {_label_for_match(row[2], row[3])!r} but Telegram could not "
            f"resolve it ({e}). Try ``telegram-tk rescan``."
        ) from e
    if sys.stderr.isatty():
        print(
            f"# list: channel {_label_for_match(row[2], row[3])} ({row[0]} id={row[1]})",
            file=sys.stderr,
        )
    return resolved


async def run(
    channel: str,
    limit: int | None,
    header: bool,
    *,
    cache_db: Path | None = None,
    max_cache_age_sec: int = 0,
    refresh: bool = False,
    name_min_score: int = 82,
    pick: int | None = None,
    output: Path | None = None,
    output_tz: str = DEFAULT_LIST_OUTPUT_TZ,
) -> None:
    if not (1 <= name_min_score <= 100):
        raise SystemExit("list: --min-score must be between 1 and 100")
    display_tz = _resolve_output_tz(output_tz)
    cache_path = cache_db or DEFAULT_CACHE
    client = make_client()
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Not authorized. Run: .venv/bin/python -m telegram_toolkit.auth")
    entity = await _resolve_entity_for_list(
        client,
        channel,
        cache_path=cache_path,
        name_min_score=name_min_score,
        pick=pick,
    )
    channel_id = utils.get_peer_id(entity)

    if not refresh and max_cache_age_sec > 0:
        conn = _open_db(cache_path)
        try:
            meta = conn.execute(
                "SELECT fetched_at_utc FROM channel_member_snapshot_meta WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
            if meta and _meta_fresh(meta[0], max_cache_age_sec):
                snap = _load_snapshot_rows(conn, channel_id)
                if snap:
                    if sys.stderr.isatty():
                        print(
                            f"# list: using cached snapshot from {meta[0]} "
                            f"({len(snap)} rows, --refresh to refetch)",
                            file=sys.stderr,
                        )
                    last_dm = _fetch_max_private_dm_iso_by_peer(conn)
                    await client.disconnect()
                    n = _write_member_rows(
                        [(r[0], r[1] or "", r[2] or "", r[3] or "", r[4]) for r in snap],
                        limit,
                        header,
                        output=output,
                        display_tz=display_tz,
                        last_private_iso_by_user=last_dm,
                    )
                    if output is not None and sys.stderr.isatty():
                        print(f"# list: wrote {n} rows to {output}", file=sys.stderr)
                    return
        finally:
            conn.close()

    rows: list[tuple[datetime, int, User, str]] = []
    try:
        async for p in client.iter_participants(entity, limit=None):
            if isinstance(p, User):
                jd, iso = _participant_join(p)
                rows.append((jd, p.id, p, iso))
    except ChatAdminRequiredError as e:
        await client.disconnect()
        raise SystemExit(
            "list: Telegram refused the member list (CHAT_ADMIN_REQUIRED). "
            "Channel lookup already matched ``name``; this error is from GetParticipants / "
            "iter_participants — megagroups often need admin rights or member visibility so "
            "your account can enumerate members."
        ) from e
    rows.sort(key=lambda t: (t[0], t[1]))
    flat: list[tuple[int, str, str, str, str, int]] = []
    for jd, _pid, u, joined in rows:
        b = _sort_bucket(jd)
        flat.append(
            (
                u.id,
                u.username or "",
                (u.first_name or "").replace("\t", " "),
                (u.last_name or "").replace("\t", " "),
                joined,
                b,
            )
        )
    await client.disconnect()

    conn = _open_db(cache_path)
    try:
        last_dm = _fetch_max_private_dm_iso_by_peer(conn)
        _replace_snapshot(conn, channel_id, flat)
        conn.commit()
    finally:
        conn.close()

    n = _write_member_rows(
        [(t[0], t[1], t[2], t[3], t[4]) for t in flat],
        limit,
        header,
        output=output,
        display_tz=display_tz,
        last_private_iso_by_user=last_dm,
    )
    if output is not None and sys.stderr.isatty():
        print(f"# list: wrote {n} rows to {output}", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(
        description=(
            "List members of a channel or megagroup (TSV to stdout, or CSV with --output). "
            "Adds last-private-chat time from the local messages cache (same DB as rescan)."
        )
    )
    p.add_argument(
        "channel",
        metavar="CHANNEL",
        help="Title fragment (name search) or numeric channel/group id (chats row or get_entity)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="After sorting by join date, emit at most this many rows (default: all)",
    )
    p.add_argument("--no-header", action="store_true", help="Omit TSV header row (easier piping to grep)")
    p.add_argument(
        "--max-cache-age",
        type=int,
        default=0,
        metavar="SEC",
        help="If >0: reuse local member snapshot newer than SEC seconds (default: 0 = always live Telegram).",
    )
    p.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore snapshot and refetch all members from Telegram",
    )
    p.add_argument(
        "--cache",
        type=Path,
        default=None,
        help=f"SQLite path: name search + optional member snapshot (default: {DEFAULT_CACHE})",
    )
    p.add_argument(
        "--min-score",
        type=int,
        default=82,
        metavar="N",
        help="Fuzzy match threshold 1–100 for name fallback (default: 82, same as telegram-tk name)",
    )
    p.add_argument(
        "--pick",
        type=int,
        default=None,
        metavar="N",
        help="When several fuzzy matches: use the Nth row (1-based) without prompting",
    )
    p.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        metavar="PATH",
        help="Write UTF-8 comma-separated CSV to this file instead of TSV to stdout",
    )
    p.add_argument(
        "--tz",
        type=str,
        default=DEFAULT_LIST_OUTPUT_TZ,
        metavar="ZONE",
        help=(
            f"IANA time zone for joined_* and last_private_* columns (default: {DEFAULT_LIST_OUTPUT_TZ}, "
            "US Pacific). Shorthand: PST, PDT, PT → same. Examples: UTC, Europe/Berlin."
        ),
    )
    args = p.parse_args()
    asyncio.run(
        run(
            args.channel,
            args.limit,
            header=not args.no_header,
            cache_db=args.cache,
            max_cache_age_sec=args.max_cache_age,
            refresh=args.refresh,
            name_min_score=args.min_score,
            pick=args.pick,
            output=args.output,
            output_tz=args.tz,
        )
    )


if __name__ == "__main__":
    main()
