"""Hostel complaint conversation flow and administrator handover commands."""
from __future__ import annotations

import json
import logging
import mimetypes
import re
import secrets
from pathlib import Path

from app import db
from app.ai_methods import explain_intent
from app.config import ADMIN_CHAT_ID, MAX_TEXT_LENGTH, UPLOAD_DIR
from app.telegram_api import download_telegram_file, send_media, send_message

logger = logging.getLogger(__name__)

MENU = {
    "keyboard": [
        [{"text": "📝 New complaint"}, {"text": "🔎 Check status"}],
        [{"text": "❓ FAQs"}, {"text": "👤 Talk to human"}],
        [{"text": "🏠 Main menu"}],
    ],
    "resize_keyboard": True,
}
CATEGORY_KEYBOARD = {
    "keyboard": [
        [{"text": "Electrical"}, {"text": "Plumbing"}],
        [{"text": "Cleaning"}, {"text": "Wi-Fi"}],
        [{"text": "Furniture"}, {"text": "Other"}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": True,
}
CATEGORY_MAP = {
    "electrical": "Electrical", "plumbing": "Plumbing", "cleaning": "Cleaning",
    "wi-fi": "Wi-Fi", "wifi": "Wi-Fi", "furniture": "Furniture", "other": "Other",
}
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".pdf", ".txt", ".doc", ".docx", ".bin"}
FAQS_PATH = Path(__file__).resolve().parents[1] / "data" / "faqs.json"
with FAQS_PATH.open("r", encoding="utf-8") as handle:
    FAQS = json.load(handle)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


async def reply(chat_id: int, text: str, keyboard: dict | None = MENU) -> None:
    await send_message(chat_id, text, keyboard)
    db.save_message(None, chat_id, None, "out", text)


async def notify_admin(text: str) -> bool:
    if ADMIN_CHAT_ID is None:
        logger.warning("ADMIN_CHAT_ID is not configured; handover notification was not sent")
        return False
    try:
        await send_message(ADMIN_CHAT_ID, text)
        db.save_message(None, ADMIN_CHAT_ID, None, "out", text, is_internal=True)
        return True
    except Exception:
        # A broken admin chat must not make Telegram retry an already-created ticket update.
        logger.exception("Could not notify the configured administrator")
        return False


def format_ticket(ticket) -> str:
    return (
        f"Ticket: {ticket['ticket_id']}\n"
        f"Category: {ticket['category']}\n"
        f"Location: {ticket['location']}\n"
        f"Status: {ticket['status']}\n"
        f"Created (UTC): {ticket['created_at']}"
    )


async def handle_admin_command(chat_id: int, text: str) -> bool:
    if ADMIN_CHAT_ID is None or chat_id != ADMIN_CHAT_ID:
        return False
    parts = text.strip().split(maxsplit=2)
    command = parts[0].lower() if parts else ""

    if command in {"/admin", "/help"}:
        await reply(
            chat_id,
            "Admin commands:\n/stats\n/reply TICKET_ID your message\n/close TICKET_ID\n/ticket TICKET_ID",
            keyboard=None,
        )
        return True

    if command == "/stats":
        await reply(chat_id, json.dumps(db.get_stats(), indent=2, ensure_ascii=False), keyboard=None)
        return True

    if command == "/ticket":
        if len(parts) < 2:
            await reply(chat_id, "Usage: /ticket TICKET_ID", keyboard=None)
            return True
        ticket = db.get_ticket(parts[1])
        await reply(chat_id, format_ticket(ticket) if ticket else "Ticket not found.", keyboard=None)
        return True

    if command == "/reply":
        if len(parts) < 3:
            await reply(chat_id, "Usage: /reply TICKET_ID your message", keyboard=None)
            return True
        ticket = db.get_ticket(parts[1])
        if not ticket:
            await reply(chat_id, "Ticket not found.", keyboard=None)
            return True
        if ticket["status"] == "closed":
            await reply(chat_id, "This ticket is already closed. Reopen it in the database workflow before replying.", keyboard=None)
            return True
        message = parts[2][:3500]
        await send_message(ticket["chat_id"], f"Hostel helpdesk update for {ticket['ticket_id']}:\n{message}")
        db.save_message(None, ticket["chat_id"], None, "out", message)
        db.update_ticket_status(parts[1], "in_progress")
        await reply(chat_id, f"Reply sent to {ticket['ticket_id']}.", keyboard=None)
        return True

    if command == "/close":
        if len(parts) < 2:
            await reply(chat_id, "Usage: /close TICKET_ID", keyboard=None)
            return True
        ticket = db.get_ticket(parts[1])
        if not ticket:
            await reply(chat_id, "Ticket not found.", keyboard=None)
            return True
        db.update_ticket_status(parts[1], "closed")
        current_session = db.get_session(int(ticket["chat_id"]))
        if current_session.get("data", {}).get("ticket_id") == parts[1].upper():
            db.set_session(int(ticket["chat_id"]), "idle", {})
        await send_message(
            ticket["chat_id"],
            f"Your ticket {parts[1].upper()} has been marked closed. Send /menu to return to the main menu.",
        )
        db.save_message(None, int(ticket["chat_id"]), None, "out", "Ticket closed by administrator.")
        await reply(chat_id, f"Ticket {parts[1].upper()} closed.", keyboard=None)
        return True
    return False


async def start_complaint(chat_id: int) -> None:
    db.set_session(chat_id, "awaiting_category", {})
    await reply(chat_id, "Choose the complaint category:", CATEGORY_KEYBOARD)


async def start_human_handover(
    chat_id: int, username: str | None, session: dict, update_id: int
) -> None:
    ticket_id = session.get("data", {}).get("ticket_id")
    ticket = db.get_ticket(ticket_id) if ticket_id else None
    if not ticket or ticket["status"] == "closed":
        ticket = db.get_latest_active_ticket_for_user(chat_id)
    if ticket:
        ticket_id = str(ticket["ticket_id"])
    else:
        ticket_id = db.create_ticket(
            chat_id, username, "General support", "Not provided",
            "User requested a human support handover.", source_update_id=update_id,
        )
    db.update_ticket_status(ticket_id, "human_requested")
    db.set_session(chat_id, "human_mode", {"ticket_id": ticket_id})

    if ADMIN_CHAT_ID is not None:
        notified = await notify_admin(
            f"HUMAN HANDOVER REQUEST\nTicket: {ticket_id}\n"
            f"User: @{username or 'no_username'}\nChat ID: {chat_id}\n"
            f"Reply: /reply {ticket_id} your message\nClose: /close {ticket_id}"
        )
        if notified:
            await reply(
                chat_id,
                f"A human support request has been sent to the administrator. Reference: {ticket_id}. "
                "You can send follow-up text or one attachment here.",
            )
        else:
            await reply(
                chat_id,
                f"Your request is saved as {ticket_id}, but the administrator could not be notified. "
                "Please contact the hostel office directly and provide this reference.",
            )
    else:
        await reply(
            chat_id,
            f"Human handover is not configured yet. Contact the hostel office directly. Reference: {ticket_id}.",
        )


async def _save_and_forward_media(
    message: dict, chat_id: int, username: str | None, session: dict
) -> None:
    session_data = session.get("data", {})
    ticket_id = session_data.get("ticket_id")
    session_state = session.get("state")
    if session_state not in {"awaiting_media", "human_mode"} or not ticket_id:
        await reply(chat_id, "To attach evidence, first create a complaint or request human support.")
        return

    media_type = "document"
    file_id = None
    original_name = None
    if message.get("photo"):
        media_type = "photo"
        file_id = message["photo"][-1].get("file_id")
        original_name = "telegram-photo.jpg"
    elif message.get("document"):
        document = message["document"]
        file_id = document.get("file_id")
        original_name = str(document.get("file_name") or "telegram-document")[:180]
    if not file_id:
        await reply(chat_id, "Unsupported attachment. Send a photo or document.")
        return

    # Use a random server-side name. Never write the user-supplied filename as a path.
    temporary_path = UPLOAD_DIR / f"{secrets.token_hex(12)}.upload"
    try:
        telegram_path, file_size = await download_telegram_file(file_id, temporary_path)
        suffix = Path(original_name or telegram_path).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            suffix = Path(telegram_path).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            suffix = ".bin"
        final_path = temporary_path.with_suffix(suffix)
        temporary_path.rename(final_path)
        db.add_attachment(
            ticket_id, chat_id, file_id, media_type, str(final_path), original_name, file_size
        )
        await reply(chat_id, f"Attachment saved for ticket {ticket_id} ({file_size} bytes).")

        if ADMIN_CHAT_ID is not None:
            caption = f"Attachment for {ticket_id} from @{username or 'no_username'} (chat {chat_id})"
            try:
                await send_media(ADMIN_CHAT_ID, final_path, media_type, caption)
                db.save_message(None, ADMIN_CHAT_ID, None, "out", caption, media_type, is_internal=True)
            except Exception:
                logger.exception("Could not forward attachment to administrator")
                await notify_admin(f"Attachment is stored on the server for ticket {ticket_id}, but Telegram forwarding failed. Check application logs.")
        if session_state == "awaiting_media":
            db.set_session(chat_id, "idle", {})
            await reply(chat_id, f"Your complaint is complete. Use 🔎 Check status to track {ticket_id}.")
    except Exception as exc:
        temporary_path.unlink(missing_ok=True)
        logger.exception("Attachment processing failed")
        await reply(chat_id, f"The attachment could not be saved ({type(exc).__name__}). Please retry or type skip if you are registering a complaint.")


async def handle_message(message: dict, update_id: int) -> None:
    chat = message.get("chat") or {}
    if not chat.get("id"):
        logger.warning("Ignoring Telegram message without chat ID")
        return
    chat_id = int(chat["id"])
    if chat.get("type") != "private":
        await reply(chat_id, "For privacy, open a private chat with this bot to register or track a hostel request.")
        return

    sender = message.get("from") or {}
    username = sender.get("username")
    text = str(message.get("text") or message.get("caption") or "").strip()
    low = normalize(text)
    photo_or_doc = bool(message.get("photo") or message.get("document"))
    media_type = "photo" if message.get("photo") else "document" if message.get("document") else None

    # The administrator can configure the bot with /id before consenting as a service user.
    if low in {"/id", "/chatid"}:
        await send_message(chat_id, f"Your Telegram chat ID is {chat_id}. Share it privately with the project administrator for configuration.")
        return

    is_admin = ADMIN_CHAT_ID is not None and chat_id == ADMIN_CHAT_ID
    if not is_admin and not db.has_consent(chat_id):
        if low.startswith("/start"):
            await send_message(
                chat_id,
                "Welcome to the Hostel Helpdesk Bot.\n\n"
                "Before using the service, please read this notice: the bot stores your Telegram chat ID, username (if available), "
                "messages, complaint details and any photo/document you submit. The information is used to register, track and "
                "forward maintenance requests to the configured helpdesk administrator. Do not send passwords, payment details "
                "or sensitive identity documents.\n\n"
                "To continue, type: I AGREE\nTo stop, type: /decline",
            )
            return
        if low in {"i agree", "agree", "/agree"}:
            db.record_consent(chat_id)
            conversation_id = db.touch_conversation(chat_id, username)
            db.save_message(update_id, chat_id, username, "in", "I AGREE", conversation_id=conversation_id)
            await reply(chat_id, "Consent recorded. You can now use the Hostel Helpdesk Bot. Choose an option below.")
            return
        if low in {"/decline", "decline"}:
            await send_message(chat_id, "No service records were created. You can close this chat; contact the hostel office directly for help.")
            return
        await send_message(chat_id, "Send /start to read the privacy notice. Type I AGREE to continue or /decline to stop.")
        return

    conversation_id = db.touch_conversation(chat_id, username)
    db.save_message(
        update_id, chat_id, username, "in", text or ("[attachment]" if photo_or_doc else "[non-text message]"),
        media_type=media_type, is_internal=is_admin, conversation_id=conversation_id,
    )

    if low == "/delete_my_data" and not is_admin:
        paths = db.delete_user_data(chat_id)
        for saved_path in paths:
            Path(saved_path).unlink(missing_ok=True)
        await send_message(chat_id, "Your helpdesk records and stored attachments have been deleted. Send /start if you want to use the service again.")
        return

    if chat_id == ADMIN_CHAT_ID and text.startswith("/"):
        if await handle_admin_command(chat_id, text):
            return

    low = normalize(text)
    session = db.get_session(chat_id)

    if low.startswith("/start") or low in {"/menu", "🏠 main menu", "main menu"}:
        db.set_session(chat_id, "idle", {})
        await reply(
            chat_id,
            "Welcome to the Hostel Helpdesk Bot.\n\n"
            "Register a maintenance complaint, check a ticket, read FAQs or request a human.\n\n"
            "Your consent is recorded. Your Telegram chat ID, username (if available), messages, ticket details and attachments are stored for helpdesk handling. "
            "Use /delete_my_data to delete your helpdesk records. Do not send passwords, payment details or sensitive identity documents.",
        )
        return
    if low in {"/cancel", "cancel"}:
        db.set_session(chat_id, "idle", {})
        await reply(chat_id, "Current conversation step cancelled. Choose an option from the menu.")
        return
    if low in {"📝 new complaint", "new complaint", "/complaint"}:
        await start_complaint(chat_id)
        return
    if low in {"🔎 check status", "check status", "/status"}:
        db.set_session(chat_id, "awaiting_ticket_id", {})
        await reply(chat_id, "Send your ticket ID. I only show tickets created by your Telegram account.", keyboard=None)
        return
    if low in {"❓ faqs", "faqs", "/faq"}:
        faq_text = "Hostel Helpdesk FAQs\n\n" + "\n\n".join(
            f"{item['question']}\n{item['answer']}" for item in FAQS
        )
        await reply(chat_id, faq_text)
        return
    if low in {"👤 talk to human", "talk to human", "/human"}:
        await start_human_handover(chat_id, username, session, update_id)
        return
    if photo_or_doc:
        await _save_and_forward_media(message, chat_id, username, session)
        return

    if session["state"] == "awaiting_category":
        category = CATEGORY_MAP.get(low)
        if not category:
            await reply(chat_id, "Choose one of the listed categories using the buttons below, or send /cancel.", CATEGORY_KEYBOARD)
            return
        db.set_session(chat_id, "awaiting_location", {"category": category})
        await reply(chat_id, "Enter your hostel block and room number (example: Block B, Room 214).", keyboard=None)
        return

    if session["state"] == "awaiting_location":
        if len(text) < 2:
            await reply(chat_id, "Please enter a valid block or location, or send /cancel.", keyboard=None)
            return
        data = session["data"]
        data["location"] = text[:150]
        db.set_session(chat_id, "awaiting_description", data)
        await reply(chat_id, "Describe the issue in a few sentences. Do not include passwords or sensitive personal information.", keyboard=None)
        return

    if session["state"] == "awaiting_description":
        if len(text) < 8:
            await reply(chat_id, "Please add more detail (at least 8 characters) so the maintenance team can understand the issue.", keyboard=None)
            return
        data = session["data"]
        ticket_id = db.create_ticket(
            chat_id, username, data["category"], data["location"], text[:MAX_TEXT_LENGTH],
            source_update_id=update_id,
        )
        db.set_session(chat_id, "awaiting_media", {"ticket_id": ticket_id})
        notified = False
        if ADMIN_CHAT_ID is not None:
            notified = await notify_admin(
                f"NEW HOSTEL COMPLAINT\nTicket: {ticket_id}\nCategory: {data['category']}\n"
                f"Location: {data['location']}\nDescription: {text[:MAX_TEXT_LENGTH]}\n"
                f"User: @{username or 'no_username'} / chat {chat_id}\nReply: /reply {ticket_id} your message"
            )
        confirmation = f"Complaint registered. Ticket ID: {ticket_id}.\n\nNow send one photo/document as evidence, or type skip to finish."
        if ADMIN_CHAT_ID is not None and not notified:
            confirmation += "\n\nThe ticket is saved, but the admin notification failed. Contact the hostel office and provide this ticket ID."
        await reply(chat_id, confirmation, keyboard=None)
        return

    if session["state"] == "awaiting_media":
        if low in {"skip", "no", "no attachment", "/skip"}:
            ticket_id = session["data"].get("ticket_id", "")
            db.set_session(chat_id, "idle", {})
            await reply(chat_id, f"Your ticket {ticket_id} is saved. Use 🔎 Check status to track it.")
        else:
            await reply(chat_id, "Send one photo or document as evidence, or type skip to finish.", keyboard=None)
        return

    if session["state"] == "awaiting_ticket_id":
        ticket = db.get_ticket_for_user(text, chat_id)
        db.set_session(chat_id, "idle", {})
        if ticket:
            await reply(chat_id, format_ticket(ticket))
        else:
            await reply(chat_id, "No ticket with that ID belongs to this Telegram account. Check the ID and try again.")
        return

    if session["state"] == "human_mode":
        ticket_id = session["data"].get("ticket_id", "unknown")
        if ADMIN_CHAT_ID is not None:
            forwarded = await notify_admin(
                f"FOLLOW-UP for ticket {ticket_id}\nUser: @{username or 'no_username'} / chat {chat_id}\n"
                f"Message: {text[:MAX_TEXT_LENGTH]}\nReply: /reply {ticket_id} your message"
            )
            if forwarded:
                await reply(chat_id, "Your message has been forwarded to the helpdesk team. A staff member can reply here.")
            else:
                await reply(chat_id, "Your ticket is saved, but forwarding failed. Please contact the hostel office and provide your ticket ID.")
        else:
            await reply(chat_id, "Human handover is not configured yet. Please contact the hostel office directly.")
        return

    trace = explain_intent(text)
    logger.info("Intent trace: %s", trace)
    matching_faq = next((item for item in FAQS if item.get("intent") == trace["intent"]), None)
    if matching_faq:
        await reply(chat_id, matching_faq["answer"] + "\n\nTo register this issue, choose 📝 New complaint.")
    else:
        await reply(chat_id, "I could not confidently classify that message. Choose an option below or select 👤 Talk to human.")
