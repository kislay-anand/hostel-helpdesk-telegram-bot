"""Small asynchronous wrapper around the Telegram Bot HTTP API."""
from __future__ import annotations

import mimetypes
from pathlib import Path

import httpx

from app.config import BOT_TOKEN, MAX_UPLOAD_BYTES

API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
FILE_BASE = f"https://api.telegram.org/file/bot{BOT_TOKEN}"


async def call(method: str, payload: dict | None = None):
    """Call a Telegram Bot API JSON method and validate its {ok, result} response."""
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it in the environment or .env file.")
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        response = await client.post(f"{API_BASE}/{method}", json=payload or {})
        response.raise_for_status()
        body = response.json()
    if not body.get("ok"):
        description = body.get("description", "Unknown Telegram API error")
        error_code = body.get("error_code", "unknown")
        raise RuntimeError(f"Telegram API {method} failed ({error_code}): {description}")
    return body.get("result")


def _split_text(text: str, limit: int = 4000) -> list[str]:
    remaining = (text or " ").strip() or " "
    pieces: list[str] = []
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        pieces.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        pieces.append(remaining)
    return pieces or [" "]


async def send_message(chat_id: int, text: str, reply_markup: dict | None = None):
    """Send text; split long messages to stay under Telegram's message limit."""
    results = []
    chunks = _split_text(text)
    for index, chunk in enumerate(chunks):
        payload = {"chat_id": chat_id, "text": chunk}
        if reply_markup and index == len(chunks) - 1:
            payload["reply_markup"] = reply_markup
        results.append(await call("sendMessage", payload))
    return results[-1] if results else None


async def get_file_info(file_id: str) -> dict:
    result = await call("getFile", {"file_id": file_id})
    if not isinstance(result, dict) or not result.get("file_path"):
        raise RuntimeError("Telegram did not return a file path.")
    return result


async def download_telegram_file(file_id: str, destination: Path) -> tuple[str, int]:
    """Download a Telegram file with a hard byte cap and no user-controlled path."""
    info = await get_file_info(file_id)
    declared_size = int(info.get("file_size") or 0)
    if declared_size > MAX_UPLOAD_BYTES:
        raise ValueError(f"Attachment exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
    file_path = str(info["file_path"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0)) as client:
            async with client.stream("GET", f"{FILE_BASE}/{file_path}") as response:
                response.raise_for_status()
                with destination.open("wb") as target:
                    async for chunk in response.aiter_bytes(64 * 1024):
                        written += len(chunk)
                        if written > MAX_UPLOAD_BYTES:
                            raise ValueError("Downloaded file exceeds the configured size limit.")
                        target.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return file_path, written


async def send_media(chat_id: int, path: Path, media_type: str, caption: str = ""):
    """Upload a stored attachment to the administrator; fall back to document if needed."""
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing.")
    if not path.is_file():
        raise FileNotFoundError("Attachment file is missing from persistent storage.")

    use_photo = (
        media_type == "photo"
        and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
        and path.stat().st_size <= 10 * 1024 * 1024
    )
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    data = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption[:900]

    async def upload(method: str, field: str):
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0)) as client:
            with path.open("rb") as handle:
                response = await client.post(
                    f"{API_BASE}/{method}",
                    data=data,
                    files={field: (path.name, handle, mime)},
                )
            response.raise_for_status()
            body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {body.get('description', 'unknown error')}")
        return body.get("result")

    if use_photo:
        try:
            return await upload("sendPhoto", "photo")
        except httpx.HTTPStatusError as exc:
            # An image rejected by sendPhoto can still be delivered safely as a document.
            if exc.response.status_code != 400:
                raise
            return await upload("sendDocument", "document")
    return await upload("sendDocument", "document")
