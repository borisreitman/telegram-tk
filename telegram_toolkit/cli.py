#!/usr/bin/env python3
"""
telegram-tk — private DM cache (SQLite) + Telegram sync + login.

Subcommands: auth, search, rescan, full-rescan, show, name, channel-member, list.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable
from pathlib import Path

from telegram_toolkit.client import make_client  # noqa: E402
from telegram_toolkit.dm_cache import (  # noqa: E402
    DEFAULT_CACHE,
    refresh_cache,
    search_local,
    show_peer,
    _CacheTraceLive,
    _TracePeerSummary,
)
from telegram_toolkit.list_users import DEFAULT_LIST_OUTPUT_TZ  # noqa: E402


async def run_auth() -> None:
    """Interactive login; writes session file from ``TELEGRAM_SESSION`` / default."""
    client = make_client()
    await client.start()
    me = await client.get_me()
    print(f"Logged in as @{me.username}" if me.username else f"Logged in as id={me.id}")
    await client.disconnect()


def _cmd_auth(_ns: argparse.Namespace) -> int:
    asyncio.run(run_auth())
    return 0


def _cmd_help(_ns: argparse.Namespace) -> int:
    build_parser().print_help()
    return 0


def _add_cache(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE,
        help=f"SQLite path (default: {DEFAULT_CACHE})",
    )


def _cmd_search(ns: argparse.Namespace) -> int:
    q = " ".join(ns.query).strip()
    if not q:
        raise SystemExit('search: pass a query string, e.g. telegram-tk search "hello"')
    search_local(ns.cache.resolve(), q, header=ns.header, verbose=ns.verbose)
    return 0


def _run_refresh(
    cache: Path,
    *,
    full: bool,
    quiet: bool,
    recent_peer_limit: int,
    sync_per_peer_limit: int | None,
    rescan_top_all: bool,
    no_bots: bool,
) -> None:
    if not full and recent_peer_limit < 1:
        raise SystemExit("rescan: --recent-peer-limit must be at least 1 (or use full-rescan).")
    if not quiet:
        print("# refreshing cache from Telegram…", file=sys.stderr)
        if full:
            print(
                "# cache  mode: full rescan (all private user chats + all channel/group chat metadata)",
                file=sys.stderr,
            )
        else:
            line = (
                f"# cache  mode: top {recent_peer_limit} private chats by recency (rescan); "
                f"channel/group rows in chats: first {recent_peer_limit} dialogs only"
            )
            if rescan_top_all:
                line += " — rescan-top-all: refresh every peer in that window"
            else:
                line += (
                    " — skip peers whose cache is only outgoing (no incoming); "
                    "use --rescan-top-all to refresh all N"
                )
            print(line, file=sys.stderr)

    trace_ui = None
    if not quiet:
        trace_ui = (
            _CacheTraceLive(sys.stderr)
            if sys.stderr.isatty()
            else _TracePeerSummary(sys.stderr)
        )

    recent_cap = None if full else recent_peer_limit
    asyncio.run(
        refresh_cache(
            cache,
            skip_bots=no_bots,
            per_peer_limit=sync_per_peer_limit,
            trace_ui=trace_ui,
            quiet=quiet,
            recent_peer_limit=recent_cap,
            rescan_top_all=rescan_top_all,
        )
    )


def _cmd_rescan(ns: argparse.Namespace) -> int:
    _run_refresh(
        ns.cache.resolve(),
        full=False,
        quiet=ns.notrace,
        recent_peer_limit=ns.recent_peer_limit,
        sync_per_peer_limit=ns.sync_per_peer_limit,
        rescan_top_all=ns.rescan_top_all,
        no_bots=ns.no_bots,
    )
    return 0


def _cmd_full_rescan(ns: argparse.Namespace) -> int:
    _run_refresh(
        ns.cache.resolve(),
        full=True,
        quiet=ns.notrace,
        recent_peer_limit=20,
        sync_per_peer_limit=ns.sync_per_peer_limit,
        rescan_top_all=False,
        no_bots=ns.no_bots,
    )
    return 0


def _cmd_show(ns: argparse.Namespace) -> int:
    if ns.user_id <= 0:
        raise SystemExit("show: USER_ID must be a positive Telegram user id.")
    asyncio.run(show_peer(ns.user_id, ns.cache.resolve(), quiet=ns.notrace))
    return 0


def _cmd_name(ns: argparse.Namespace) -> int:
    from telegram_toolkit.find_dm_peer import run_find_dm_peer

    q = " ".join(ns.name).strip()
    return run_find_dm_peer(
        q,
        cache=ns.cache,
        header=ns.header,
        min_score=ns.min_score,
        channel=ns.channel,
    )


def _cmd_channel_member(ns: argparse.Namespace) -> int:
    from telegram_toolkit.channel_member import run_channel_member_cli

    code = run_channel_member_cli(ns)
    if ns.ok_if_not_member:
        return 0
    return code


def _cmd_list(ns: argparse.Namespace) -> int:
    from telegram_toolkit.list_users import run as list_users_run

    asyncio.run(
        list_users_run(
            ns.channel,
            ns.limit,
            header=not ns.no_header,
            cache_db=ns.cache,
            max_cache_age_sec=ns.max_cache_age,
            refresh=ns.refresh,
            name_min_score=ns.min_score,
            pick=ns.pick,
            output=ns.output,
            output_tz=ns.tz,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="telegram-tk",
        description="Private DM SQLite cache and Telegram helpers.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "auth",
        help="Interactive Telegram login (creates/updates the .session file)",
    )

    sub.add_parser(
        "help",
        help="Show this help message and exit",
    )

    sp = sub.add_parser("search", help="Search cached private message text (no Telegram I/O)")
    _add_cache(sp)
    sp.add_argument(
        "--verbose",
        action="store_true",
        help="One TSV row per matching message (id, date, text)",
    )
    sp.add_argument(
        "--header",
        action="store_true",
        help="Print column header row (default: off)",
    )
    sp.add_argument(
        "query",
        nargs="+",
        metavar="TEXT",
        help="Substring to find (Unicode case-insensitive)",
    )

    rp = sub.add_parser(
        "rescan",
        help="Fetch new messages from Telegram for the top N recent private chats",
    )
    _add_cache(rp)
    rp.add_argument(
        "--sync-per-peer-limit",
        type=int,
        default=None,
        metavar="N",
        help="On first cache of a peer, fetch at most N newest messages (default: all)",
    )
    rp.add_argument(
        "--recent-peer-limit",
        type=int,
        default=20,
        metavar="N",
        help="Only the N most recent private user dialogs (default: 20)",
    )
    rp.add_argument(
        "--rescan-top-all",
        action="store_true",
        help="Refresh every peer in the top-N window (default: skip if cache has only your outgoing)",
    )
    rp.add_argument(
        "--no-bots",
        action="store_true",
        help="Skip 1:1 chats with bots",
    )
    rp.add_argument(
        "--notrace",
        action="store_true",
        help="Suppress stderr progress (spinner / # cache lines)",
    )

    fp = sub.add_parser(
        "full-rescan",
        help="Fetch from Telegram for every private user chat",
    )
    _add_cache(fp)
    fp.add_argument(
        "--sync-per-peer-limit",
        type=int,
        default=None,
        metavar="N",
        help="On first cache of a peer, fetch at most N newest messages (default: all)",
    )
    fp.add_argument(
        "--no-bots",
        action="store_true",
        help="Skip 1:1 chats with bots",
    )
    fp.add_argument(
        "--notrace",
        action="store_true",
        help="Suppress stderr progress (spinner / # cache lines)",
    )

    sh = sub.add_parser("show", help="Print cache + Telegram info for a user id")
    _add_cache(sh)
    sh.add_argument(
        "user_id",
        type=int,
        metavar="USER_ID",
        help="Telegram peer_user_id",
    )
    sh.add_argument(
        "--notrace",
        action="store_true",
        help="Suppress stderr (# show … line)",
    )

    fdp = sub.add_parser(
        "name",
        help="Find dialogs by name or peer id (cache; fuzzy names; id shows peer_id and title)",
    )
    _add_cache(fdp)
    fdp.add_argument(
        "name",
        nargs="+",
        metavar="TEXT",
        help="Name fragment (transliterate Russian, then fuzzy), or numeric / -100… / marked id",
    )
    fdp.add_argument(
        "--min-score",
        type=int,
        default=82,
        metavar="N",
        help="rapidfuzz WRatio per word, 1–100 (default: 82)",
    )
    fdp.add_argument(
        "--header",
        action="store_true",
        help="Print TSV header row",
    )
    fdp.add_argument(
        "--channel",
        metavar="CHANNEL",
        help="Limit search to members of this channel (requires channel-member snapshots in cache)",
    )

    cm = sub.add_parser(
        "channel-member",
        help="Print whether each user id is a member of a channel / megagroup (TSV)",
    )
    cm.add_argument("channel", help="@username, t.me link, or numeric id")
    cm.add_argument(
        "--id",
        action="append",
        default=[],
        metavar="USER_ID",
        help="User id (repeatable)",
    )
    cm.add_argument(
        "--file",
        metavar="PATH",
        help="File with one user id per line (or TSV; first column)",
    )
    cm.add_argument(
        "--no-header",
        action="store_true",
        help="Omit TSV header row",
    )
    cm.add_argument(
        "--ok-if-not-member",
        action="store_true",
        help="Exit 0 even when some ids are not members",
    )

    lu = sub.add_parser(
        "list",
        help="List all members of a channel or megagroup (TSV to stdout, or CSV via --output)",
    )
    lu.add_argument(
        "channel",
        metavar="CHANNEL",
        help="Title (same as telegram-tk name), or channel/group id: plain id, -100…, or marked -id",
    )
    lu.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="After sorting by join date, emit at most N rows (default: all)",
    )
    lu.add_argument(
        "--no-header",
        action="store_true",
        help="Omit TSV header row",
    )
    _add_cache(lu)
    lu.add_argument(
        "--max-cache-age",
        type=int,
        default=0,
        metavar="SEC",
        help="If >0: reuse local member snapshot newer than SEC s (default: 0 = always fetch members live).",
    )
    lu.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore snapshot and refetch all members from Telegram",
    )
    lu.add_argument(
        "--min-score",
        type=int,
        default=82,
        metavar="N",
        help="Fuzzy threshold 1–100 for name fallback (default: 82, same as name)",
    )
    lu.add_argument(
        "--pick",
        type=int,
        default=None,
        metavar="N",
        help="If several fuzzy matches: use the Nth match (1-based) without prompting",
    )
    lu.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        metavar="PATH",
        help="Write UTF-8 comma-separated CSV to this file instead of TSV to stdout",
    )
    lu.add_argument(
        "--tz",
        type=str,
        default=DEFAULT_LIST_OUTPUT_TZ,
        metavar="ZONE",
        help=(
            f"IANA zone for joined_* and last_private_* columns (default: {DEFAULT_LIST_OUTPUT_TZ}, "
            "US Pacific). Shorthand: PST, PDT, PT. Examples: UTC, Europe/Berlin."
        ),
    )

    return p


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = build_parser()
    ns = p.parse_args(argv)
    handlers: dict[str, Callable[[argparse.Namespace], int]] = {
        "auth": _cmd_auth,
        "help": _cmd_help,
        "search": _cmd_search,
        "rescan": _cmd_rescan,
        "full-rescan": _cmd_full_rescan,
        "show": _cmd_show,
        "name": _cmd_name,
        "channel-member": _cmd_channel_member,
        "list": _cmd_list,
    }
    return handlers[ns.command](ns)


if __name__ == "__main__":
    raise SystemExit(main())
