"""Shared Telethon client wiring (env-based)."""
from __future__ import annotations

import os

from telethon import TelegramClient


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Set environment variable {name} (see .env.example).")
    return value


def make_client() -> TelegramClient:
    api_id = int(_require_env("TELEGRAM_API_ID"))
    api_hash = _require_env("TELEGRAM_API_HASH")
    session = os.environ.get("TELEGRAM_SESSION", "telegram_session").strip() or "telegram_session"
    return TelegramClient(session, api_id, api_hash)
