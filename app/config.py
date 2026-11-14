"""Environment configuration for the Hostel Helpdesk bot."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")


def _int_env(name: str, default: int | None = None) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer Telegram chat ID") from exc


def _ids_env(name: str) -> set[int]:
    values: set[int] = set()
    for part in os.getenv(name, "").split(","):
        item = part.strip()
        if not item:
            continue
        try:
            values.add(int(item))
        except ValueError as exc:
            raise RuntimeError(f"{name} must be comma-separated integer chat IDs") from exc
    return values


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
ADMIN_CHAT_ID = _int_env("ADMIN_CHAT_ID")
ADMIN_KEY = os.getenv("ADMIN_KEY", "").strip()
TEAM_CHAT_IDS = _ids_env("TEAM_CHAT_IDS")

DB_PATH_RAW = Path(os.getenv("DB_PATH", "data/hostel_bot.sqlite3"))
DB_PATH = DB_PATH_RAW if DB_PATH_RAW.is_absolute() else ROOT_DIR / DB_PATH_RAW
UPLOAD_DIR_RAW = Path(os.getenv("UPLOAD_DIR", "uploads"))
UPLOAD_DIR = UPLOAD_DIR_RAW if UPLOAD_DIR_RAW.is_absolute() else ROOT_DIR / UPLOAD_DIR_RAW

# Telegram's standard cloud Bot API getFile download limit.
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
MAX_TEXT_LENGTH = 1500
CONVERSATION_GAP_MINUTES = int(os.getenv("CONVERSATION_GAP_MINUTES", "30"))
UPDATE_PROCESSING_TTL_SECONDS = int(os.getenv("UPDATE_PROCESSING_TTL_SECONDS", "300"))


def validate_runtime_config() -> None:
    """Fail early when required credentials are absent or obviously invalid."""
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it in your hosting environment or .env file.")
    if len(WEBHOOK_SECRET) < 16:
        raise RuntimeError("WEBHOOK_SECRET must contain at least 16 characters.")
    if not ADMIN_KEY or len(ADMIN_KEY) < 16:
        raise RuntimeError("ADMIN_KEY must contain at least 16 characters.")
    if MAX_UPLOAD_BYTES < 1024:
        raise RuntimeError("MAX_UPLOAD_BYTES is too small.")
