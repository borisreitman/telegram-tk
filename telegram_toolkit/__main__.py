"""``python -m telegram_toolkit`` → same as ``telegram-tk`` (including legacy bare-query)."""
from __future__ import annotations

import sys

from telegram_toolkit.cli import main as cli_main

_SUBCOMMANDS = frozenset(
    {
        "auth",
        "search",
        "rescan",
        "full-rescan",
        "show",
        "name",
        "channel-member",
        "list",
    }
)


def main() -> int:
    argv = sys.argv[1:]
    if len(argv) == 1 and argv[0] in ("-h", "--help"):
        return cli_main(argv)
    if argv and argv[0] not in _SUBCOMMANDS:
        argv = ["search", *argv]
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
