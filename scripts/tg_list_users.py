#!/usr/bin/env python3
"""
List channel / supergroup members as TSV (grep-friendly).

Columns: user_id, username, first_name, last_name, is_bot

Example:
  set -a && source .env && set +a
  .venv/bin/python scripts/tg_list_users.py @mychannel | grep -i pattern
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import os
import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent
_env = _repo_root / ".env"
if _env.is_file():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v

from telethon.tl.types import User

from tg_client import make_client


async def run(channel: str, limit: int | None, header: bool) -> None:
    client = make_client()
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Not authorized. Run: .venv/bin/python scripts/tg_auth.py")
    entity = await client.get_entity(channel)
    out = io.StringIO()
    w = csv.writer(out, delimiter="\t", lineterminator="\n")
    if header:
        w.writerow(["user_id", "username", "first_name", "last_name", "is_bot"])
    n = 0
    async for p in client.iter_participants(entity, limit=limit):
        if not isinstance(p, User):
            continue
        w.writerow(
            [
                p.id,
                p.username or "",
                (p.first_name or "").replace("\t", " "),
                (p.last_name or "").replace("\t", " "),
                "1" if p.bot else "0",
            ]
        )
        n += 1
        if n % 5000 == 0:
            sys.stdout.write(out.getvalue())
            out = io.StringIO()
            w = csv.writer(out, delimiter="\t", lineterminator="\n")
    sys.stdout.write(out.getvalue())
    await client.disconnect()


def main() -> None:
    p = argparse.ArgumentParser(description="List members of a channel or megagroup (TSV).")
    p.add_argument("channel", help="@username, t.me link, or numeric id")
    p.add_argument("--limit", type=int, default=None, help="Max users (default: all)")
    p.add_argument("--no-header", action="store_true", help="Omit TSV header row (easier piping to grep)")
    args = p.parse_args()
    asyncio.run(run(args.channel, args.limit, header=not args.no_header))


if __name__ == "__main__":
    main()
