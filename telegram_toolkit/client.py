"""Shared Telethon client wiring (env-based)."""
from __future__ import annotations

import os
from pathlib import Path

from telethon import TelegramClient

from telegram_toolkit._paths import REPO_ROOT


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Set environment variable {name} (see .env.example).")
    return value


def make_client() -> TelegramClient:
    api_id = int(_require_env("TELEGRAM_API_ID"))
    api_hash = _require_env("TELEGRAM_API_HASH")
    session_val = os.environ.get("TELEGRAM_SESSION", "telegram_session").strip() or "telegram_session"

    # If the session path is not absolute, make it relative to REPO_ROOT
    # so the session file stays with the app regardless of where it's called from.
    session_path = Path(session_val)
    if not session_path.is_absolute():
        session_path = REPO_ROOT / session_path

    return TelegramClient(str(session_path), api_id, api_hash)
