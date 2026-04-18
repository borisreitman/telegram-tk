#!/usr/bin/env python3
"""
List channel / supergroup members as TSV on stdout (grep-friendly), or as CSV
when ``--output`` is set.

Rows are sorted by **join date** (from Telethon ``user.participant.date`` when present),
then by ``user_id``. ``ChannelParticipantCreator`` / ``ChatParticipantCreator`` have no
join date and sort first. Rows without a usable date sort last.

Columns: user_id, username, first_name, last_name, joined_date, joined_time,
last_private_date, last_private_time (same output time zone, default US Pacific ``America/Los_Angeles``;
empty when join time or private unknown). Use ``--tz`` for an IANA zone (``UTC``, ``Europe/Berlin``, …).

**Resolving CHANNEL**: (1) numeric **peer id** — database exact match, else ``get_entity``
on ``-100…`` / ``-id`` forms; (2) otherwise **same** fuzzy search as
``name``, keeping only **channel** / **supergroup** /
**group** hits. **Listing members** uses ``iter_participants`` (Telegram);
``--max-cache-age`` / ``--refresh`` only affect optional local **member** snapshots.

One listable name match resolves automatically; several require ``--pick N`` or a TTY prompt.
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
from telegram_toolkit.resolver import resolve_listable_entity

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
            f"error: unknown time zone {name!r} (resolved {key!r}). "
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
    Used to correlate channel members with the local 1:1 message cache.
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
    header: bool = False,
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


async def run(
    channel: str,
    limit: int | None,
    header: bool = False,
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
        raise SystemExit("error: --min-score must be between 1 and 100")
    display_tz = _resolve_output_tz(output_tz)
    cache_path = cache_db or DEFAULT_CACHE
    client = make_client()
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Not authorized. Run: python -m telegram_toolkit auth")
    entity = await resolve_listable_entity(
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
                            f"# using cached snapshot from {meta[0]} "
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
                        print(f"# wrote {n} rows to {output}", file=sys.stderr)
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
            "error: Telegram refused the member list (CHAT_ADMIN_REQUIRED). "
            "Megagroups often need admin rights or member visibility to enumerate members."
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
        print(f"# wrote {n} rows to {output}", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(
        description=(
            "List members of a channel or megagroup (TSV to stdout, or CSV with --output). "
            "Adds last-private-chat time from the local messages."
        )
    )
    p.add_argument(
        "channel",
        metavar="CHANNEL",
        help="Title fragment, @username, or numeric ID.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="After sorting by join date, emit at most this many rows (default: all)",
    )
    p.add_argument(
        "--max-cache-age",
        type=int,
        default=0,
        metavar="SEC",
        help="If >0: reuse local member snapshot newer than SEC seconds (default: 0 = always live).",
    )
    p.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore snapshot and refetch all members from Telegram",
    )
    p.add_argument(
        "--min-score",
        type=int,
        default=82,
        metavar="N",
        help="Fuzzy match threshold 1–100 for name fallback (default: 82)",
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
            cache_db=None,
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
