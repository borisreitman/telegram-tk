#!/usr/bin/env python3
"""
List all messages in a private chat with a given user (your outgoing + his incoming).

Output is TSV: message_id, date (ISO UTC), direction (out|in), sender_id, text_or_placeholder

Examples:
  .venv/bin/python -m telegram_toolkit.list_user_messages @someuser
  .venv/bin/python -m telegram_toolkit.list_user_messages maks_bilt
  .venv/bin/python -m telegram_toolkit.list_user_messages 8718875571 --from-channel @YourChannel
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import sys

from telethon.tl.types import User

from telegram_toolkit.client import make_client


def _parse_user_id(spec: str) -> int | None:
    s = spec.strip().removeprefix("@")
    if s.isdigit():
        return int(s)
    return None


async def _resolve_user_entity(client: object, user: str, from_channel: str | None) -> User:
    uid = _parse_user_id(user)
    if uid is None:
        ent = await client.get_entity(user)
        if not isinstance(ent, User):
            raise SystemExit("Entity is not a user; pass @username, phone, or numeric user id.")
        return ent

    try:
        ent = await client.get_entity(uid)
        if isinstance(ent, User):
            return ent
    except (ValueError, TypeError):
        pass

    if from_channel:
        chan = await client.get_entity(from_channel)
        async for p in client.iter_participants(chan):
            if isinstance(p, User) and p.id == uid:
                return p

    async for dialog in client.iter_dialogs():
        if dialog.is_user and isinstance(dialog.entity, User) and dialog.entity.id == uid:
            return dialog.entity

    if not from_channel:
        hint = (
            " Pass --from-channel @Channel where he appears (same place you exported the id),"
            " or use his @username."
        )
    else:
        hint = " Not in that channel's member list or your dialogs; try @username if you know it."
    raise SystemExit(f"Cannot resolve user id {uid}.{hint}")


def _one_line(text: str) -> str:
    return (text or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")


def _text_or_placeholder(message: object) -> str:
    t = getattr(message, "message", None) or getattr(message, "text", None) or ""
    if isinstance(t, str) and t.strip():
        return t
    if getattr(message, "media", None) is None:
        return ""
    m = message.media
    return f"[{type(m).__name__}]"


async def run(
    user: str,
    from_channel: str | None,
    limit: int | None,
    oldest_first: bool,
    header: bool,
) -> None:
    client = make_client()
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Not authorized. Run: .venv/bin/python -m telegram_toolkit auth")
    entity = await _resolve_user_entity(client, user, from_channel)
    out = io.StringIO()
    w = csv.writer(out, delimiter="\t", lineterminator="\n")
    if header:
        w.writerow(["message_id", "date_utc", "direction", "sender_id", "text"])
    n = 0
    async for message in client.iter_messages(entity, limit=limit, reverse=oldest_first):
        direction = "out" if message.out else "in"
        sid = message.sender_id or ""
        row = [
            message.id,
            message.date.isoformat() if message.date else "",
            direction,
            sid,
            _one_line(_text_or_placeholder(message)),
        ]
        w.writerow(row)
        n += 1
        if n % 2000 == 0:
            sys.stdout.write(out.getvalue())
            out = io.StringIO()
            w = csv.writer(out, delimiter="\t", lineterminator="\n")
    sys.stdout.write(out.getvalue())
    await client.disconnect()


def main() -> None:
    p = argparse.ArgumentParser(
        description="List messages in a 1:1 chat with a user (sent and received)."
    )
    p.add_argument(
        "user",
        help="@username, phone, t.me link, or numeric user id (see --from-channel for raw ids)",
    )
    p.add_argument(
        "--from-channel",
        metavar="CHANNEL",
        help="If user is a numeric id: resolve him via this channel/supergroup's member list",
    )
    p.add_argument("--limit", type=int, default=None, help="Max messages (default: all)")
    p.add_argument(
        "--newest-first",
        action="store_true",
        help="Walk from newest to oldest (default: oldest first)",
    )
    p.add_argument("--no-header", action="store_true", help="Omit TSV header row")
    args = p.parse_args()
    asyncio.run(
        run(
            args.user,
            args.from_channel,
            args.limit,
            oldest_first=not args.newest_first,
            header=not args.no_header,
        )
    )


if __name__ == "__main__":
    main()
