#!/usr/bin/env python3
"""
Check whether given Telegram user ids are participants of a channel or megagroup.

Prints TSV: ``user_id``, ``member`` (``0`` / ``1``). Uses Telethon ``get_permissions``;
``UserNotParticipantError`` means not a member (or left / kicked).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

from telethon import errors

from telegram_toolkit.client import make_client


def _parse_ids(args: argparse.Namespace) -> list[int]:
    ids: list[int] = []
    for raw in args.id:
        ids.append(int(raw))
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            part = line.split()[0].split("\t")[0].strip()
            if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
                ids.append(int(part))
    if not sys.stdin.isatty():
        for line in sys.stdin:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            part = line.split()[0].split("\t")[0].strip()
            if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
                ids.append(int(part))
    seen: set[int] = set()
    out: list[int] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


async def run_channel_member(
    channel: str,
    user_ids: list[int],
    *,
    with_header: bool,
) -> int:
    if not user_ids:
        raise SystemExit("channel-member: pass user ids via --id, --file, or stdin")
    client = make_client()
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise SystemExit("Not authorized. Run: .venv/bin/python -m telegram_toolkit auth")
    ch = await client.get_entity(channel)
    w = csv.writer(sys.stdout, delimiter="\t", lineterminator="\n")
    if with_header:
        w.writerow(["user_id", "member"])
    worst = 0
    try:
        for uid in user_ids:
            member = False
            err = ""
            try:
                await client.get_permissions(ch, uid)
                member = True
            except errors.UserNotParticipantError:
                member = False
            except errors.ChatAdminRequiredError as e:
                err = str(e)
                worst = max(worst, 2)
            except Exception as e:  # noqa: BLE001
                err = str(e)
                worst = max(worst, 2)
            w.writerow([uid, "1" if member else "0"])
            if err:
                print(f"# user_id={uid} error: {err}", file=sys.stderr)
            elif not member:
                worst = max(worst, 1)
    finally:
        await client.disconnect()
    return worst


def run_channel_member_cli(ns: argparse.Namespace) -> int:
    """Used by ``telegram-tk channel-member`` (same flags as :func:`main`)."""
    user_ids = _parse_ids(ns)
    return asyncio.run(
        run_channel_member(ns.channel, user_ids, with_header=not ns.no_header)
    )


def main() -> None:
    p = argparse.ArgumentParser(
        description="Check if user ids are members of a channel / megagroup (read-only)."
    )
    p.add_argument("channel", help="@username, t.me link, or numeric id")
    p.add_argument("--id", action="append", default=[], metavar="USER_ID", help="User id (repeatable)")
    p.add_argument("--file", metavar="PATH", help="File with one user id per line (or TSV; first column)")
    p.add_argument(
        "--no-header",
        action="store_true",
        help="Omit TSV header row",
    )
    p.add_argument(
        "--ok-if-not-member",
        action="store_true",
        help="Always exit 0 (still prints member column)",
    )
    args = p.parse_args()
    code = run_channel_member_cli(args)
    if args.ok_if_not_member:
        raise SystemExit(0)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
