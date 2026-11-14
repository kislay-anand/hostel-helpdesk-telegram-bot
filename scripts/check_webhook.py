"""Read Telegram's current webhook registration and recent delivery error."""
from pathlib import Path
import os

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not TOKEN:
    raise SystemExit("BOT_TOKEN is missing.")
response = requests.get(f"https://api.telegram.org/bot{TOKEN}/getWebhookInfo", timeout=30)
response.raise_for_status()
body = response.json()
if not body.get("ok"):
    raise SystemExit(str(body))
info = body.get("result", {})
print(f"URL: {info.get('url') or '(not configured)'}")
print(f"Pending updates: {info.get('pending_update_count', 0)}")
print(f"Last error date: {info.get('last_error_date')}")
print(f"Last error: {info.get('last_error_message')}")
print(f"Max connections: {info.get('max_connections')}")
