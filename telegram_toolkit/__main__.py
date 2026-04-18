"""``python -m telegram_toolkit`` → same as ``telegram-tk`` (including legacy bare-query)."""
from __future__ import annotations

import sys

from telegram_toolkit.cli import main as cli_main

_SUBCOMMANDS = frozenset(
    {
        "auth",
        "help",
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
    # If no arguments or first argument is not a subcommand, argparse will handle it.
    # We removed the legacy "default to search" logic as requested.
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
