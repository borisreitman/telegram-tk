#!/usr/bin/env bash
#
# Wrapper for telegram-tk (Telethon DM cache CLI).
# Runs: python -m telegram_toolkit (same CLI as ./telegram-tk in the repo root).
#
# Install: copy to ~/bin and make executable, e.g.
#   cp doc/telegram-tk.sh ~/bin/telegram-tk
#   chmod +x ~/bin/telegram-tk
#
# Configure where your clone lives (pick one):
#   export TELEGRAM_TK_REPO=/absolute/path/to/telegram   # in ~/.bashrc / ~/.zshrc
#   or edit the default below.
#
set -euo pipefail

: "${TELEGRAM_TK_REPO:=${HOME}/work/telegram}"

cd "${TELEGRAM_TK_REPO}" || {
  echo "telegram-tk.sh: cannot cd to TELEGRAM_TK_REPO=${TELEGRAM_TK_REPO}" >&2
  exit 1
}

if [[ ! -f .venv/bin/activate ]]; then
  echo "telegram-tk.sh: missing .venv in ${TELEGRAM_TK_REPO}" >&2
  exit 1
fi

# shellcheck source=/dev/null
source .venv/bin/activate

exec python -m telegram_toolkit "$@"
