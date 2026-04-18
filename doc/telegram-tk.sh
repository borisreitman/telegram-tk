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

if [[ ! -d "${TELEGRAM_TK_REPO}" ]]; then
  echo "telegram-tk: TELEGRAM_TK_REPO=${TELEGRAM_TK_REPO} does not exist" >&2
  exit 1
fi

VENV_PYTHON="${TELEGRAM_TK_REPO}/.venv/bin/python"

if [[ ! -f "${VENV_PYTHON}" ]]; then
  echo "telegram-tk: missing .venv python in ${TELEGRAM_TK_REPO}" >&2
  exit 1
fi

# Run with PYTHONPATH set to the repo root so we can find telegram_toolkit
# while keeping the user's CWD for relative paths in arguments.
export PYTHONPATH="${TELEGRAM_TK_REPO}:${PYTHONPATH:-}"

exec "${VENV_PYTHON}" -m telegram_toolkit "$@"
