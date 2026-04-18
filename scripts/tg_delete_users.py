#!/usr/bin/env python3
"""
Ban (remove) users from a channel or megagroup. Requires admin + ban users right.

User IDs: one per line on stdin, or --file, or repeated --id.

Examples:
  echo 123456 | .venv/bin/python scripts/tg_delete_users.py @mychannel --dry-run
  .venv/bin/python scripts/tg_delete_users.py @mychannel --yes --file ids.txt
  grep -i spam users.tsv | cut -f1 | .venv/bin/python scripts/tg_delete_users.py @mychannel --yes
"""
from __future__ import annotations

import argparse
import asyncio
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

from tg_client import make_client


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
            # TSV: take first column
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
    # de-dupe preserving order
    seen: set[int] = set()
    out: list[int] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


async def run(channel: str, user_ids: list[int], dry_run: bool, yes: bool) -> None:
    if not user_ids:
        raise SystemExit("No user ids provided (stdin, --file, or --id).")
    if not dry_run and not yes:
        raise SystemExit("Refusing to ban without --yes (use --dry-run to preview).")

    client = make_client()
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Not authorized. Run: .venv/bin/python scripts/tg_auth.py")
    entity = await client.get_entity(channel)

    for uid in user_ids:
        if dry_run:
            print(f"dry-run: would ban user_id={uid}")
            continue
        try:
            await client.edit_permissions(entity, uid, view_messages=False)
            print(f"banned user_id={uid}")
        except Exception as e:  # noqa: BLE001 — surface RPC errors to operator
            print(f"error user_id={uid}: {e}", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(description="Ban users from a channel / megagroup by user id.")
    p.add_argument("channel", help="@username, t.me link, or numeric id")
    p.add_argument("--id", action="append", default=[], metavar="USER_ID", help="User id (repeatable)")
    p.add_argument("--file", metavar="PATH", help="File with one user id per line (or TSV; first column)")
    p.add_argument("--dry-run", action="store_true", help="Print actions only")
    p.add_argument("--yes", action="store_true", help="Confirm ban (required unless --dry-run)")
    args = p.parse_args()
    user_ids = _parse_ids(args)
    asyncio.run(run(args.channel, user_ids, args.dry_run, args.yes))


if __name__ == "__main__":
    main()
