"""FastAPI app: Telegram webhook, health check and a token-protected stats endpoint."""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text as sql_text

from .bot import Bot
from .config import Settings, get_settings
from .db import Attachment, init_db, make_engine, make_session_factory, User
from .stats import compute_stats
from .telegram import Outbound, Result, TelegramClient, TelegramError

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("helpbot")
logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines contain the bot token in the URL

BOT_COMMANDS = [
    {"command": "new", "description": "File a hostel complaint"},
    {"command": "mycomplaints", "description": "See my complaints"},
    {"command": "status", "description": "Check a complaint's status"},
    {"command": "human", "description": "Talk to hostel staff"},
    {"command": "help", "description": "What can this bot do?"},
    {"command": "cancel", "description": "Cancel what I'm doing"},
    {"command": "why", "description": "How did the bot understand my message?"},
    {"command": "privacy", "description": "How my data is used"},
]


class App:
    """Holds long-lived objects so tests can build an app with a fake Telegram client."""

    def __init__(self, settings: Settings, tg: TelegramClient | None = None):
        self.settings = settings
        self.engine = make_engine(settings.database_url)
        init_db(self.engine)
        self.sf = make_session_factory(self.engine)
        self.bot = Bot(settings, self.sf)
        self.tg = tg or TelegramClient(settings.bot_token, settings.telegram_api_base)

    # ---------------------------------------------------------------- sending with recovery
    async def deliver(self, res: Result) -> None:
        """Perform each Telegram call. One failing call never blocks the rest."""
        for out in res.out:
            try:
                await self.tg.send(out)
            except TelegramError as e:
                if e.user_blocked:
                    self._mark_blocked(out.payload.get("chat_id"))
                else:
                    log.error("send failed: %s", e)
            except Exception:  # noqa: BLE001
                log.exception("unexpected error while sending %s", out.method)
        for att_id, file_id in res.downloads:
            await self._download(att_id, file_id)

    def _mark_blocked(self, chat_id: int | None) -> None:
        if chat_id is None:
            return
        with self.sf() as db:
            for u in db.query(User).filter(User.chat_id == chat_id):
                u.blocked = True
            db.commit()

    async def _download(self, att_id: int, file_id: str) -> None:
        s = self.settings
        try:
            path = await self.tg.download_file(file_id, s.media_dir, s.max_file_mb * 1024 * 1024)
        except Exception as e:  # noqa: BLE001
            log.warning("media download failed for attachment %s: %s", att_id, e)
            return
        if path:
            with self.sf() as db:
                a = db.get(Attachment, att_id)
                if a:
                    a.local_path = path
                    db.commit()

    async def process(self, update: dict) -> None:
        try:
            res = await run_in_threadpool(self.bot.handle_update, update)
        except Exception as e:  # noqa: BLE001  - error recovery: apologise, alert staff, never loop
            log.exception("handler crashed for update %s", update.get("update_id"))
            await run_in_threadpool(self.bot.mark_processed, update["update_id"])
            await self._recover(update, e)
            return
        await self.deliver(res)

    async def _recover(self, update: dict, err: Exception) -> None:
        chat_id = ((update.get("message") or {}).get("chat") or {}).get("id") or \
                  (((update.get("callback_query") or {}).get("message") or {}).get("chat") or {}).get("id")
        calls = []
        if chat_id and chat_id != self.settings.admin_chat_id:
            calls.append(Outbound("sendMessage", {"chat_id": chat_id, "text":
                "⚠️ Sorry, something went wrong on my side. Please try again in a moment, or type /human to reach staff."}))
        if self.settings.admin_chat_id:
            calls.append(Outbound("sendMessage", {"chat_id": self.settings.admin_chat_id, "text":
                f"⚠️ Bot error on update {update.get('update_id')}: {type(err).__name__}: {str(err)[:200]}"}))
        await self.deliver(Result(out=calls))

    async def setup_telegram(self) -> None:
        s = self.settings
        if not s.bot_token:
            log.warning("BOT_TOKEN is empty — Telegram calls will fail")
            return
        try:
            me = await self.tg.call("getMe")
            log.info("running as @%s", me.get("username"))
            await self.tg.call("setMyCommands", {"commands": BOT_COMMANDS})
            if s.auto_set_webhook and s.public_url:
                url = s.public_url.rstrip("/") + "/webhook"
                payload = {"url": url, "allowed_updates": ["message", "callback_query", "my_chat_member"],
                           "drop_pending_updates": False, "max_connections": 20}
                if s.webhook_secret:
                    payload["secret_token"] = s.webhook_secret
                await self.tg.call("setWebhook", payload)
                log.info("webhook registered at %s", url)
        except TelegramError as e:
            log.error("Telegram setup failed (bot will still accept webhooks): %s", e)

    async def janitor(self) -> None:
        while True:
            await asyncio.sleep(600)
            try:
                log.info("cleanup: %s", await run_in_threadpool(self.bot.cleanup))
            except Exception:  # noqa: BLE001
                log.exception("cleanup failed")


def create_app(settings: Settings | None = None, tg: TelegramClient | None = None, background: bool = True) -> FastAPI:
    settings = settings or get_settings()
    core = App(settings, tg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if background:
            await core.setup_telegram()
            task = asyncio.create_task(core.janitor())
        yield
        if task:
            task.cancel()
        await core.tg.close()

    app = FastAPI(title="Hostel HelpBot", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.core = core

    @app.get("/")
    async def root() -> PlainTextResponse:
        return PlainTextResponse("Hostel HelpBot is running. Open Telegram and send /start to the bot.")

    @app.get("/health")
    async def health() -> dict:
        def check() -> bool:
            with core.sf() as db:
                db.execute(sql_text("select 1"))
            return True
        try:
            ok = await run_in_threadpool(check)
        except Exception:  # noqa: BLE001
            return JSONResponse({"status": "degraded", "db": False}, status_code=503)
        return {"status": "ok", "db": ok}

    @app.post("/webhook")
    async def webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
        if settings.webhook_secret and not hmac.compare_digest(x_telegram_bot_api_secret_token or "", settings.webhook_secret):
            raise HTTPException(status_code=403, detail="bad secret")
        try:
            update = await request.json()
            update["update_id"]
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="invalid update")
        await core.process(update)
        return {"ok": True}  # always 200 so Telegram doesn't retry-storm; duplicates are de-duplicated anyway

    @app.get("/api/stats")
    async def stats(x_admin_token: str | None = Header(default=None)):
        if not settings.admin_api_token or not hmac.compare_digest(x_admin_token or "", settings.admin_api_token):
            raise HTTPException(status_code=401, detail="unauthorized")

        def run():
            with core.sf() as db:
                return compute_stats(db, settings)
        return await run_in_threadpool(run)

    return app


_cache: dict = {}


# `uvicorn app.main:app` — built lazily so that importing this module (e.g. in tests) has no side effects.
def __getattr__(name: str):
    if name == "app":
        if "app" not in _cache:
            _cache["app"] = create_app()
        return _cache["app"]
    raise AttributeError(name)
