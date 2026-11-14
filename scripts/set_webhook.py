"""Register the deployed HTTPS webhook with Telegram."""
from __future__ import annotations

import os
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

TOKEN = os.getenv("BOT_TOKEN", "").strip()
SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")

if not TOKEN or not SECRET or not BASE_URL:
    raise SystemExit("Set BOT_TOKEN, WEBHOOK_SECRET and PUBLIC_BASE_URL before registering the webhook.")
if not BASE_URL.startswith("https://"):
    raise SystemExit("PUBLIC_BASE_URL must begin with https://")

webhook_url = f"{BASE_URL}/telegram/webhook"
response = requests.post(
    f"https://api.telegram.org/bot{TOKEN}/setWebhook",
    json={
        "url": webhook_url,
        "secret_token": SECRET,
        "allowed_updates": ["message"],
        "drop_pending_updates": False,
    },
    timeout=30,
)
response.raise_for_status()
body = response.json()
if not body.get("ok"):
    raise SystemExit(f"Telegram rejected the webhook: {body}")
print(f"Webhook registered: {webhook_url}")
print(body)
