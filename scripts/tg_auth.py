#!/usr/bin/env python3
"""One-time (or occasional) login: creates the .session file (default: current working directory)."""
from __future__ import annotations

import asyncio
import os

try:
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent.parent
    env_path = _repo_root / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
except OSError:
    pass

from tg_client import make_client


async def main() -> None:
    client = make_client()
    await client.start()
    me = await client.get_me()
    print(f"Logged in as @{me.username}" if me.username else f"Logged in as id={me.id}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
