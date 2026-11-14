"""FastAPI app: health check, secured Telegram webhook and admin analytics."""
from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app import db
from app.bot import handle_message
from app.config import ADMIN_KEY, WEBHOOK_SECRET, UPLOAD_DIR

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hostel-helpdesk")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="Hostel Helpdesk Telegram Bot",
    description="Webhook-based hostel maintenance complaint and human handover service.",
    version="2.0.0",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    return {"service": "Hostel Helpdesk Telegram Bot", "status": "running", "docs": "/docs"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if not WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook secret is not configured")
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(supplied, WEBHOOK_SECRET):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    try:
        update = await request.json()
        if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
            raise HTTPException(status_code=400, detail="Invalid Telegram update payload")
        update_id = int(update["update_id"])
        try:
            claimed = db.claim_update(update_id)
        except db.UpdateInProgressError:
            # A second delivery arrived while the first is still running. Tell Telegram to retry
            # rather than acknowledging a request that might be lost if the first worker crashes.
            return JSONResponse(status_code=503, content={"ok": False, "detail": "Update is already processing"})
        if not claimed:
            return {"ok": True, "duplicate": True}
        try:
            message = update.get("message")
            if message:
                await handle_message(message, update_id)
            # This project uses reply keyboards rather than inline callbacks.
            db.mark_update_processed(update_id)
            return {"ok": True}
        except Exception:
            db.mark_update_failed(update_id)
            raise
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Webhook processing failed")
        # Telegram retries non-2xx responses. Ticket creation is keyed by update_id to avoid duplicate tickets.
        return JSONResponse(status_code=500, content={"ok": False, "error": type(exc).__name__})


@app.get("/admin/stats")
async def admin_stats(x_admin_key: str = Header(default="")):
    if not ADMIN_KEY or not hmac.compare_digest(x_admin_key, ADMIN_KEY):
        raise HTTPException(status_code=403, detail="Forbidden")
    return db.get_stats()
