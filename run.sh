#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .venv/bin/activate ]]; then
  echo "Missing .venv — create it and install deps first." >&2
  exit 1
fi
# shellcheck source=/dev/null
source .venv/bin/activate

python -m telegram_toolkit search "Крымский жребий"

# Other examples (uncomment):
# python -m telegram_toolkit rescan
# python -m telegram_toolkit rescan --notrace --recent-peer-limit 50
# python -m telegram_toolkit full-rescan
# python -m telegram_toolkit show 15840524
# ./telegram-tk search "hello"   # if ./telegram-tk is executable
