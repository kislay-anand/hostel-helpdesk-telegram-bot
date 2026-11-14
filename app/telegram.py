"""Thin async Telegram Bot API client with retry, rate-limit and blocked-user handling."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("helpbot.telegram")


@dataclass
class Outbound:
    """A Telegram API call the bot wants made. The bot logic only *describes* calls;
    main.py performs them. This keeps the logic testable without a network."""
    method: str
    payload: dict[str, Any]


@dataclass
class Result:
    out: list[Outbound] = field(default_factory=list)
    downloads: list[tuple[int, str]] = field(default_factory=list)  # (attachment id, file_id)
    blocked_chat: int | None = None


class TelegramError(Exception):
    def __init__(self, method: str, code: int | None, description: str):
        super().__init__(f"{method}: {code} {description}")
        self.method, self.code, self.description = method, code, description

    @property
    def user_blocked(self) -> bool:
        return self.code == 403


class TelegramClient:
    def __init__(self, token: str, api_base: str = "https://api.telegram.org", max_retries: int = 3):
        self.base = f"{api_base}/bot{token}"
        self.file_base = f"{api_base}/file/bot{token}"
        self.max_retries = max_retries
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))

    async def close(self) -> None:
        await self._http.aclose()

    async def call(self, method: str, payload: dict | None = None) -> Any:
        """POST a Bot API method. Retries on 429 (honouring retry_after), 5xx and network errors."""
        last: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                r = await self._http.post(f"{self.base}/{method}", json=payload or {})
                data = r.json() if r.content else {}
                if r.status_code == 200 and data.get("ok"):
                    return data.get("result")
                code = data.get("error_code", r.status_code)
                desc = data.get("description", r.text[:200])
                if code == 429:
                    wait = min(int((data.get("parameters") or {}).get("retry_after", 1)), 10)
                    log.warning("429 on %s, sleeping %ss", method, wait)
                    await asyncio.sleep(wait)
                    last = TelegramError(method, code, desc)
                    continue
                if code >= 500:
                    last = TelegramError(method, code, desc)
                    await asyncio.sleep(0.5 * 2 ** (attempt - 1))
                    continue
                raise TelegramError(method, code, desc)  # 4xx: retrying will not help
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = e
                log.warning("network error on %s (attempt %d): %s", method, attempt, e)
                await asyncio.sleep(0.5 * 2 ** (attempt - 1))
        raise TelegramError(method, None, f"gave up after {self.max_retries} attempts: {last}")

    async def send(self, out: Outbound) -> Any:
        return await self.call(out.method, out.payload)

    async def download_file(self, file_id: str, dest_dir: str, max_bytes: int) -> str | None:
        """Download a file to dest_dir; returns the local path (None if too large)."""
        import os
        info = await self.call("getFile", {"file_id": file_id})
        if info.get("file_size", 0) > max_bytes:
            return None
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, f"{info['file_unique_id']}_{os.path.basename(info['file_path'])}")
        r = await self._http.get(f"{self.file_base}/{info['file_path']}")
        r.raise_for_status()
        with open(path, "wb") as f:
            f.write(r.content)
        return path
