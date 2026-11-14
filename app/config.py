"""Runtime configuration, read from environment variables (or a local .env file)."""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Telegram ---
    bot_token: str = ""
    # Sent back by Telegram in the X-Telegram-Bot-Api-Secret-Token header on every webhook call.
    # Allowed characters: A-Z a-z 0-9 _ -   (1-256 chars)
    webhook_secret: str = ""
    # Public HTTPS base URL of the deployed service, e.g. https://hostel-helpbot.onrender.com
    public_url: str = ""
    auto_set_webhook: bool = True
    telegram_api_base: str = "https://api.telegram.org"

    # --- Humans ---
    # Chat (group or private) that receives complaints and handover requests.
    admin_chat_id: int | None = None
    # Telegram user ids allowed to run admin commands (comma separated).
    admin_user_ids: str = ""
    # Team members' user ids: their chats are excluded from "genuine user" statistics.
    team_user_ids: str = ""
    # Token protecting GET /api/stats (send as header X-Admin-Token). Empty = endpoint disabled.
    admin_api_token: str = ""

    # --- Storage ---
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'helpbot.db'}"
    download_media: bool = False
    media_dir: str = str(BASE_DIR / "data" / "media")
    max_file_mb: int = 10
    max_attachments: int = 3

    # --- Behaviour ---
    session_timeout_min: int = 15
    rate_limit_per_min: int = 20
    tfidf_threshold: float = 0.42
    timezone: str = "Asia/Kolkata"
    conversation_gap_min: int = 30
    distress_alert_admin: bool = True
    hostel_info_path: str = str(BASE_DIR / "data" / "hostel_info.json")

    @field_validator("database_url")
    @classmethod
    def _fix_pg_scheme(cls, v: str) -> str:
        # Render/Heroku style URLs use postgres://, SQLAlchemy wants postgresql+psycopg2://
        if v.startswith("postgres://"):
            return "postgresql+psycopg2://" + v[len("postgres://"):]
        if v.startswith("postgresql://"):
            return "postgresql+psycopg2://" + v[len("postgresql://"):]
        return v

    @field_validator("webhook_secret")
    @classmethod
    def _check_secret(cls, v: str) -> str:
        if v and not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", v):
            raise ValueError("WEBHOOK_SECRET may only contain A-Z a-z 0-9 _ - (Telegram's rule). "
                             "Generate one with: python -c \"import secrets; print(secrets.token_hex(24))\"")
        return v

    @model_validator(mode="after")
    def _default_public_url(self) -> "Settings":
        # Render exposes the service URL as RENDER_EXTERNAL_URL; use it when PUBLIC_URL is not set.
        if not self.public_url:
            self.public_url = os.getenv("RENDER_EXTERNAL_URL", "")
        return self

    @staticmethod
    def _ids(raw: str) -> set[int]:
        return {int(x) for x in raw.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()}

    @property
    def admin_ids(self) -> set[int]:
        return self._ids(self.admin_user_ids)

    @property
    def team_ids(self) -> set[int]:
        return self._ids(self.team_user_ids)


@lru_cache
def get_settings() -> Settings:
    return Settings()
