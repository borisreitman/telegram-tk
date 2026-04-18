"""``python -m telegram_toolkit`` → same as ``telegram-tk``."""
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
        "list",
    }
)


def main() -> int:
    argv = sys.argv[1:]
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
