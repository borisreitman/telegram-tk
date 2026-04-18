"""Shared entity resolution logic for Telegram toolkit."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from telethon import TelegramClient

from telegram_toolkit.find_dm_peer import (
    LISTABLE_PEER_KINDS,
    _label_for_match,
    fetch_listable_chat_row_by_peer_id,
    fetch_listable_chat_row_by_username,
    name_search_hits,
    parse_peer_id_literal_for_chats_lookup,
)


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
            f"error: no channel/supergroup/group in hits for {query!r}. "
            f"Run 'rescan' if the chat is missing."
        )
    if len(hits) == 1:
        return hits[0]
    if pick is not None:
        if 1 <= pick <= len(hits):
            return hits[pick - 1]
        raise SystemExit(f"error: --pick must be between 1 and {len(hits)} (got {pick})")
    if not sys.stdin.isatty():
        lines = "\n".join(
            f"  {i}. {_label_for_match(t[2], t[3])}  ({t[0]} id={t[1]})"
            for i, t in enumerate(hits, 1)
        )
        raise SystemExit(
            f"error: ambiguous name {query!r} ({len(hits)} matches). "
            "Use --pick N, pass @username / id, or run in a terminal to choose interactively.\n"
            f"{lines}"
        )
    print(f"# no direct match for {query!r}; choose a channel/group:", file=sys.stderr)
    for i, t in enumerate(hits, 1):
        print(f"  {i}. {_label_for_match(t[2], t[3])}  ({t[0]} id={t[1]})", file=sys.stderr)
    while True:
        try:
            raw = input(f"Select [1-{len(hits)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(1)
        if not raw.isdigit():
            print("Enter a positive number.", file=sys.stderr)
            continue
        n = int(raw)
        if 1 <= n <= len(hits):
            return hits[n - 1]
        print(f"Enter a number from 1 to {len(hits)}.", file=sys.stderr)


async def resolve_listable_entity(
    client: TelegramClient,
    identifier: str,
    *,
    cache_path: Path,
    name_min_score: int = 82,
    pick: int | None = None,
):
    raw = identifier.strip()
    if not raw:
        raise SystemExit("error: identifier must be non-empty")
    resolved_cache = cache_path.resolve()
    if not resolved_cache.is_file():
        raise SystemExit(f"error: no database at {resolved_cache}. Run 'rescan' first.")

    row: tuple[str, int, str, str] | None = None
    parsed_id = parse_peer_id_literal_for_chats_lookup(raw)
    if parsed_id is not None:
        row = fetch_listable_chat_row_by_peer_id(resolved_cache, parsed_id)

    if row is None:
        row = fetch_listable_chat_row_by_username(resolved_cache, raw)

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
                    print(f"# resolved id {raw!r} via get_entity({ref})", file=sys.stderr)
                return ent
            except BaseException as e:
                if isinstance(e, (asyncio.CancelledError, KeyboardInterrupt)):
                    raise
                last_err = e
        raise SystemExit(
            f"error: id {raw!r} not in database and Telegram get_entity failed: {last_err}"
        ) from last_err

    if row is None:
        raise SystemExit(
            f"error: no listable channel/group for {raw!r}. "
            "Try @username, numeric ID, or title search."
        )

    ref = _marked_entity_ref_for_list_row(row[0], row[1], row[3])
    try:
        resolved = await client.get_entity(ref)
    except BaseException as e:
        if isinstance(e, (asyncio.CancelledError, KeyboardInterrupt)):
            raise
        raise SystemExit(
            f"error: database row {_label_for_match(row[2], row[3])!r} found but Telegram failed to "
            f"resolve it ({e}). Try 'rescan'."
        ) from e
    if sys.stderr.isatty():
        print(
            f"# channel {_label_for_match(row[2], row[3])} ({row[0]} id={row[1]})",
            file=sys.stderr,
        )
    return resolved
