"""Bot logic. `Bot.handle_update(update_dict)` is synchronous and network-free: it reads/writes the
database and returns a `Result` describing which Telegram API calls to make. main.py performs them.

Conversation features
  * complaint flow with slot filling (category -> room -> description -> media -> confirm)
  * info answers (mess, laundry, wifi, warden, rules, fees) from data/hostel_info.json
  * session management (DB-backed state, inactivity timeout, cancel / restart anywhere)
  * media handling (photo/document/video/voice, size + type validation, optional download)
  * human handover (relay both ways, /reply /close for staff, /endchat for the student)
  * emergency detection that alerts staff immediately
"""
from __future__ import annotations

import html
import json
import logging
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .db import (Attachment, ChatSession, Complaint, Feedback, Handover, Message, ProcessedUpdate, User,
                 utcnow)
from .nlu import (HybridNLU, UNKNOWN, Prediction, guess_category, looks_urgent, parse_room)
from .telegram import Outbound, Result

log = logging.getLogger("helpbot.bot")
esc = html.escape

CATEGORY_LABELS = {
    "electrical": "⚡ Electrical", "plumbing": "🚿 Plumbing", "cleaning": "🧹 Cleaning",
    "internet": "📶 Internet", "furniture": "🛏 Furniture", "mess": "🍽 Mess", "other": "❓ Other",
}
STATUS_LABELS = {"open": "🟡 Open", "in_progress": "🔧 In progress", "resolved": "✅ Resolved", "rejected": "❌ Rejected"}
CMD_TO_INTENT = {"new": "file_complaint", "mine": "my_complaints", "mess": "mess_timing", "wifi": "wifi",
                 "human": "talk_to_human", "rules": "hostel_rules", "laundry": "laundry", "contact": "warden_contact",
                 "menu": "mess_menu", "fees": "fees"}
ADMIN_CMDS = {"tickets", "ticket", "setstatus", "resolve", "reply", "close", "handovers", "stats", "adminhelp"}
ALLOWED_DOC_MIME = ("image/", "video/", "application/pdf")
MENU_KB = {"inline_keyboard": [
    [{"text": "📝 New complaint", "callback_data": "cmd:new"}, {"text": "🔎 My complaints", "callback_data": "cmd:mine"}],
    [{"text": "🍽 Mess", "callback_data": "cmd:mess"}, {"text": "📶 Wi-Fi", "callback_data": "cmd:wifi"}],
    [{"text": "📜 Rules", "callback_data": "cmd:rules"}, {"text": "🙋 Talk to staff", "callback_data": "cmd:human"}],
]}


def kb(*rows: list[tuple[str, str]]) -> dict:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


class Bot:
    def __init__(self, settings: Settings, sf: sessionmaker, nlu: HybridNLU | None = None):
        self.s = settings
        self.sf = sf
        self.nlu = nlu or HybridNLU(threshold=settings.tfidf_threshold)
        self.info = json.loads(Path(settings.hostel_info_path).read_text(encoding="utf-8"))
        self.tz = ZoneInfo(settings.timezone)
        placeholders = [k for k, v in self.info.items() if isinstance(v, str) and ("00000" in v or "[ADD CONTACT HERE]" in v)]
        if self.info.get("hostel_name") == "Your Hostel Name":
            placeholders.append("hostel_name")
        if placeholders:  # students must never be shown fake emergency/warden numbers
            log.warning("data/hostel_info.json still has PLACEHOLDER values in %s — edit them before going live!", placeholders)
        self._hits: dict[int, deque] = defaultdict(deque)  # chat_id -> recent request timestamps
        self._warned: dict[int, float] = {}

    # ------------------------------------------------------------------ entry point
    def handle_update(self, update: dict) -> Result:
        with self.sf() as db:
            try:
                db.add(ProcessedUpdate(update_id=update["update_id"]))
                db.flush()
            except IntegrityError:
                db.rollback()
                log.info("duplicate update %s ignored", update.get("update_id"))
                return Result()
            h = _Handler(self, db, update)
            try:
                h.run()
                h.finish()
                db.commit()
            except Exception:
                db.rollback()
                raise
            return h.res

    def mark_processed(self, update_id: int) -> None:
        """Used after a crash so a poison update is not redelivered forever."""
        with self.sf() as db:
            try:
                db.add(ProcessedUpdate(update_id=update_id))
                db.commit()
            except IntegrityError:
                db.rollback()

    def rate_limited(self, chat_id: int) -> bool:
        now = time.monotonic()
        q = self._hits[chat_id]
        while q and now - q[0] > 60:
            q.popleft()
        q.append(now)
        return len(q) > self.s.rate_limit_per_min

    # ------------------------------------------------------------------ maintenance
    def cleanup(self) -> dict:
        """Expire idle sessions and prune old idempotency keys. Called periodically."""
        cutoff = utcnow() - timedelta(minutes=self.s.session_timeout_min)
        with self.sf() as db:
            stale = db.scalars(select(ChatSession).where(ChatSession.state != "idle", ChatSession.mode == "bot",
                                                         ChatSession.updated_at < cutoff)).all()
            for sess in stale:
                sess.state, sess.data = "idle", {}
            n_upd = db.execute(delete(ProcessedUpdate).where(ProcessedUpdate.created_at < utcnow() - timedelta(days=2))).rowcount
            db.commit()
            return {"expired_sessions": len(stale), "pruned_updates": n_upd}


class _Handler:
    """Handles exactly one Telegram update."""

    def __init__(self, bot: Bot, db: Session, update: dict):
        self.b, self.s, self.db, self.update = bot, bot.s, db, update
        self.res = Result()
        self.t0 = time.perf_counter()
        self.chat_id: int = 0
        self.user: User | None = None
        self.sess: ChatSession | None = None
        self.in_msg: Message | None = None
        self.expired = False

    # ============================================================== output helpers
    def send(self, chat_id: int, text: str, markup: dict | None = None, log_out: bool = True, **meta) -> Message | None:
        payload = {"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True}
        m = None
        if log_out:
            m = Message(chat_id=chat_id, direction="out", text=re.sub(r"<[^>]+>", "", text)[:2000], kind="text", **meta)
            self.db.add(m)
            self.db.flush()
        if markup:
            payload["reply_markup"] = markup
        self.res.out.append(Outbound("sendMessage", payload))
        return m

    def say(self, text: str, markup: dict | None = None, rate: bool = False, **meta) -> None:
        """Reply in the current chat. rate=True adds a thumbs up/down row to the message."""
        m = self.send(self.chat_id, text, markup, **meta)
        if rate and m is not None:
            row = [{"text": "👍", "callback_data": f"fb:{m.id}:1"}, {"text": "👎", "callback_data": f"fb:{m.id}:-1"}]
            rows = list((markup or {}).get("inline_keyboard", [])) + [row]
            self.res.out[-1].payload["reply_markup"] = {"inline_keyboard": rows}

    def admin_alert(self, text: str, markup: dict | None = None) -> bool:
        if not self.s.admin_chat_id:
            log.warning("admin chat not configured; alert dropped: %s", text[:80])
            return False
        self.send(self.s.admin_chat_id, text, markup, log_out=False)
        return True

    def set_state(self, state: str, **updates) -> None:
        data = dict(self.sess.data or {})
        data.update(updates)
        self.sess.state, self.sess.data, self.sess.updated_at = state, data, utcnow()

    def reset_flow(self) -> None:
        self.sess.state, self.sess.data, self.sess.updated_at = "idle", {}, utcnow()
        self.db.execute(delete(Attachment).where(Attachment.chat_id == self.chat_id, Attachment.complaint_id.is_(None)))

    def is_admin(self, user_id: int | None) -> bool:
        ids = set(self.s.admin_ids)
        if self.s.admin_chat_id and self.s.admin_chat_id > 0:
            ids.add(self.s.admin_chat_id)  # admin chat is a private chat -> that user is the admin
        return user_id in ids

    # ============================================================== dispatch
    def run(self) -> None:
        u = self.update
        if "callback_query" in u:
            return self.on_callback(u["callback_query"])
        if "my_chat_member" in u:
            return self.on_member_update(u["my_chat_member"])
        msg = u.get("message")
        if not msg:  # edited messages, channel posts etc. are ignored
            return
        chat, frm = msg["chat"], msg.get("from") or {}
        self.chat_id = chat["id"]
        text = (msg.get("text") or "").strip()
        cmd, arg = self.parse_command(text)

        if chat.get("type") in ("group", "supergroup", "channel"):
            if chat["id"] == self.s.admin_chat_id:
                return self.on_admin(msg, cmd, arg)
            if cmd == "id":
                self.say(f"Chat id: <code>{chat['id']}</code>", log_out=False)
            return  # the bot only serves private chats (and the admin group)

        self.user = self.upsert_user(frm, chat["id"])
        if self.user.blocked:
            self.user.blocked = False  # they wrote to us again, so they unblocked the bot
        # staff (admin ids) may use admin commands or reply to handover notices from a private chat
        if self.is_admin(frm.get("id")) and (cmd in ADMIN_CMDS or self.handover_ref(msg)):
            return self.on_admin(msg, cmd, arg)

        self.sess = self.db.get(ChatSession, self.chat_id) or self.new_session()
        self.log_inbound(msg, text, cmd)

        if self.b.rate_limited(self.chat_id):
            last = self.b._warned.get(self.chat_id, 0)
            if time.monotonic() - last > 60:
                self.b._warned[self.chat_id] = time.monotonic()
                self.say("⏳ You're sending messages very fast. Please slow down a little.", log_out=False)
            return

        # inactivity timeout (bot mode only; a human chat stays open until closed)
        if self.sess.mode == "bot" and self.sess.state != "idle" and \
                utcnow() - self.sess.updated_at > timedelta(minutes=self.s.session_timeout_min):
            self.reset_flow()
            self.expired = True
            self.say(f"⌛ Your earlier request timed out after {self.s.session_timeout_min} minutes of inactivity, "
                     "so I cleared it. Let's start fresh.")
        self.sess.updated_at = utcnow()  # any activity keeps the session alive

        if self.sess.mode == "human":
            return self.relay_to_staff(msg, text, cmd)
        if cmd:
            return self.on_command(cmd, arg)
        if any(k in msg for k in ("photo", "document", "video", "voice", "video_note", "audio")):
            return self.on_media(msg)
        if any(k in msg for k in ("sticker", "location", "contact", "animation", "poll")) and not text:
            return self.say("I can read text, photos, documents, videos and voice notes. Could you type that out?")
        if not text:
            return
        self.on_text(text)

    def finish(self) -> None:
        if self.in_msg is not None:
            self.in_msg.latency_ms = (time.perf_counter() - self.t0) * 1000

    # ============================================================== bookkeeping
    @staticmethod
    def parse_command(text: str) -> tuple[str | None, str]:
        if not text.startswith("/"):
            return None, ""
        head, _, rest = text.partition(" ")
        return head[1:].split("@")[0].lower(), rest.strip()

    def upsert_user(self, frm: dict, chat_id: int) -> User:
        u = self.db.get(User, frm["id"])
        name = frm.get("first_name")
        if u is None:
            u = User(id=frm["id"], chat_id=chat_id, first_name=name, username=frm.get("username"),
                     is_team=frm["id"] in self.s.team_ids or frm["id"] in self.s.admin_ids)
            self.db.add(u)
        else:
            u.chat_id, u.first_name, u.username, u.last_seen = chat_id, name or u.first_name, frm.get("username"), utcnow()
        return u

    def new_session(self) -> ChatSession:
        s = ChatSession(chat_id=self.chat_id, state="idle", data={}, mode="bot", fail_count=0, updated_at=utcnow())
        self.db.add(s)
        return s

    def log_inbound(self, msg: dict, text: str, cmd: str | None) -> None:
        kind = "command" if cmd else next((k for k in ("photo", "document", "video", "voice", "video_note", "audio",
                                                         "sticker", "location", "contact") if k in msg), "text")
        self.in_msg = Message(chat_id=self.chat_id, user_id=self.user.id, direction="in",
                              text=(text or msg.get("caption") or f"[{kind}]")[:2000], kind=kind,
                              intent="command:" + cmd if cmd else None, method="command" if cmd else None)
        self.db.add(self.in_msg)

    def tag(self, intent: str, method: str, conf: float | None = None) -> None:
        if self.in_msg is not None:
            self.in_msg.intent, self.in_msg.method, self.in_msg.confidence = intent, method, conf

    # ============================================================== commands
    def on_command(self, cmd: str, arg: str) -> None:
        if cmd == "start":
            self.reset_flow()
            return self.say(self.welcome(), MENU_KB)
        if cmd in ("help", "menu"):
            return self.say(self.help_text(), MENU_KB)
        if cmd in ("new", "newcomplaint", "complaint"):
            return self.start_complaint(None)
        if cmd in ("mycomplaints", "mine"):
            return self.show_my_complaints()
        if cmd == "status":
            return self.show_status(arg)
        if cmd in ("cancel", "stop"):
            return self.cancel()
        if cmd in ("human", "staff", "agent"):
            return self.start_handover("user_request")
        if cmd == "endchat":
            return self.say("You're not in a chat with staff right now.")
        if cmd == "id":
            return self.say(f"Your Telegram user id: <code>{self.user.id}</code>\nChat id: <code>{self.chat_id}</code>", log_out=False)
        if cmd == "privacy":
            return self.say(self.privacy_text())
        if cmd == "why":
            return self.explain_last(arg)
        if cmd == "deletemydata":
            return self.say("This removes your chat history and profile from the bot. Complaint records are kept for "
                            "hostel maintenance, but your name and username are erased from them.\n\nContinue?",
                            kb([("🗑 Yes, delete my data", "del:yes"), ("Keep it", "del:no")]))
        if cmd in CMD_TO_INTENT:
            return self.dispatch(CMD_TO_INTENT[cmd], "", Prediction(CMD_TO_INTENT[cmd], 1.0, "rule"))
        self.say("I don't know that command. Type /help to see what I can do.", MENU_KB)

    def welcome(self) -> str:
        n = esc(self.user.first_name or "there")
        return (f"👋 Hi {n}! I'm the <b>{esc(self.b.info['hostel_name'])} HelpBot</b>.\n\n"
                "I can register room complaints (with photos), track their status, and answer questions about the mess, "
                "laundry, Wi-Fi, hostel rules and contacts. If I can't help, I'll connect you to the warden's office.\n\n"
                "ℹ️ Conversations are logged so staff can resolve complaints and to measure how well the bot works. "
                "Don't share passwords or other sensitive details. /privacy for more.")

    def help_text(self) -> str:
        return ("<b>What you can do</b>\n"
                "📝 /new – file a complaint (you can attach photos)\n"
                "🔎 /mycomplaints – list your complaints\n"
                "📍 /status &lt;id&gt; – status of one complaint\n"
                "🙋 /human – talk to hostel staff\n"
                "❌ /cancel – stop what we're doing\n"
                "🔍 /why – how did you understand my last message?\n"
                "🔒 /privacy · 🗑 /deletemydata\n\n"
                "Or just type naturally, e.g. <i>“fan not working in room 204”</i> or <i>“what's for dinner?”</i>")

    def explain_last(self, text: str) -> None:
        """Transparency: show how the last (or a given) message was understood."""
        if not text:
            last = self.db.scalars(select(Message).where(
                Message.chat_id == self.chat_id, Message.direction == "in", Message.kind == "text")
                .order_by(Message.id.desc())).first()
            text = (last.text or "") if last else ""
        if not text:
            return self.say("Send me a message first, then type /why to see how I understood it.")
        ex = self.b.nlu.explain(text)
        rules = ", ".join(f"“{esc(r['matched'])}” → {r['intent']}" for r in ex["rules_fired"][:3]) or "none matched"
        near = "\n".join(f"  {i}. “{esc(n['example'])}” → {n['intent']} ({n['score']})" for i, n in enumerate(ex["neighbours"], 1))
        self.say(f"🔍 <b>How I understood:</b> “{esc(text[:120])}”\n"
                 f"Result: <b>{ex['intent']}</b> (method: {ex['method']}, confidence {ex['confidence']})\n"
                 f"Why: {esc(ex['reason'])}\n"
                 f"Keyword rules: {rules}\n"
                 f"Closest examples I know:\n{near}\n\n"
                 "<i>I'm an automated bot, not a person. Type /human any time to reach staff.</i>")

    def privacy_text(self) -> str:
        return ("<b>Privacy</b>\nThis bot stores your Telegram id, first name/username, the messages you send here, and "
                "any photos or files you attach to a complaint. Hostel staff can see complaints and handover chats. Messages "
                "are also used for anonymous usage statistics. Use /deletemydata to erase your chat history and profile.")

    def cancel(self) -> None:
        if self.sess.state == "idle":
            return self.say("Nothing to cancel. What would you like to do?", MENU_KB)
        self.reset_flow()
        self.say("Okay, cancelled. ✅ Anything else I can help with?", MENU_KB)

    # ============================================================== text routing
    def on_text(self, text: str) -> None:
        t0 = time.perf_counter()
        pred = self.b.nlu.predict(text)
        nlu_ms = (time.perf_counter() - t0) * 1000
        log.debug("nlu %.2fms %s", nlu_ms, pred)

        # Safety intents are honoured in any state, even in the middle of a form.
        if pred.intent == "distress":
            self.tag("distress", pred.method, pred.confidence)
            return self.distress()
        if pred.intent == "emergency":
            self.tag("emergency", pred.method, pred.confidence)
            return self.emergency(text)
        if pred.intent == "talk_to_human":
            self.tag("talk_to_human", pred.method, pred.confidence)
            return self.start_handover("user_request")
        if pred.intent == "cancel" and pred.method == "rule" and self.sess.state != "idle":  # explicit words only
            self.tag("cancel", pred.method, pred.confidence)
            return self.cancel()

        if self.sess.state != "idle":
            self.tag(f"flow:{self.sess.state}", "flow")
            return self.on_flow_text(text)

        self.tag(pred.intent, pred.method, pred.confidence)
        self.dispatch(pred.intent, text, pred)

    def dispatch(self, intent: str, text: str, pred: Prediction) -> None:
        info = self.b.info
        if intent != UNKNOWN:
            self.sess.fail_count = 0
        if intent == "greeting":
            return self.say(self.welcome(), MENU_KB)
        if intent == "help":
            return self.say(self.help_text(), MENU_KB)
        if intent == "goodbye":
            return self.say("Bye! 👋 Message me any time you need something.")
        if intent == "thanks":
            return self.say("You're welcome! 😊 Anything else?", MENU_KB)
        if intent == "file_complaint":
            return self.start_complaint(text or None)
        if intent == "check_status":
            return self.show_status(text)
        if intent == "my_complaints":
            return self.show_my_complaints()
        if intent == "mess_timing":
            return self.say(f"🍽 <b>Mess timings</b>\n{esc(info['mess_timings'])}", kb([("📋 Today's menu", "cmd:menu")]), rate=True)
        if intent == "mess_menu":
            return self.say(self.menu_text(text), rate=True)
        if intent == "laundry":
            return self.say(f"🧺 <b>Laundry</b>\n{esc(info['laundry'])}", rate=True)
        if intent == "wifi":
            return self.say(f"📶 <b>Wi-Fi</b>\n{esc(info['wifi'])}", kb([("📝 Report a Wi-Fi problem", "cmd:new")]), rate=True)
        if intent == "warden_contact":
            return self.say(f"☎️ <b>Contacts</b>\n{esc(info['warden_contact'])}", rate=True)
        if intent == "hostel_rules":
            return self.say(f"📜 <b>Hostel rules</b>\n{esc(info['rules'])}", rate=True)
        if intent == "fees":
            return self.say(f"💳 <b>Fees</b>\n{esc(info['fees'])}", rate=True)
        if intent == "talk_to_human":
            return self.start_handover("user_request")
        if intent == "emergency":
            return self.emergency(text)
        if intent == "distress":
            return self.distress()
        if intent == "cancel":
            return self.cancel()
        if intent == "feedback":
            self.set_state("feedback")
            return self.say("💡 Please type your suggestion or feedback and I'll pass it to the warden's office.")
        return self.fallback()

    def menu_text(self, text: str) -> str:
        menu = self.b.info["mess_menu"]
        now = datetime.now(self.b.tz)
        low = text.lower()
        if "week" in low:
            return "📋 <b>Weekly menu</b>\n" + "\n".join(f"<b>{d}</b>: {esc(v)}" for d, v in menu.items())
        day = now + timedelta(days=1) if "tomorrow" in low else now
        name = day.strftime("%A")
        label = "tomorrow" if "tomorrow" in low else "today"
        return f"📋 <b>Menu for {label} ({name})</b>\n{esc(menu.get(name, 'Not available'))}"

    def fallback(self) -> None:
        self.sess.fail_count += 1
        if self.sess.fail_count >= 2:
            return self.say("I'm still not sure what you mean — sorry! Would you like to speak to a person?",
                            kb([("🙋 Talk to staff", "cmd:human")], [("📋 Show menu", "cmd:menu_main")]))
        self.say("🤔 I didn't quite get that. You can describe a problem (e.g. “tap leaking in room 204”), ask about the "
                 "mess/Wi-Fi/rules, or pick an option:", MENU_KB)

    # ============================================================== complaint flow
    def start_complaint(self, text: str | None) -> None:
        data: dict = {"attach": []}
        if text and len(text.split()) >= 4:
            data["description"] = text[:1500]
            data["urgent"] = looks_urgent(text)
            cat = guess_category(text)
            if cat:
                data["category"] = cat
        self.sess.data = {}
        self.set_state("category", **data)
        if "category" in data:
            return self.ask_room(intro=f"Got it — I'll log this as <b>{CATEGORY_LABELS[data['category']]}</b>.\n")
        self.ask_category()

    def ask_category(self) -> None:
        self.set_state("category")
        cats = list(CATEGORY_LABELS)
        rows = [[(CATEGORY_LABELS[c], f"cat:{c}") for c in cats[i:i + 2]] for i in range(0, len(cats), 2)]
        rows.append([("❌ Cancel", "flow:cancel")])
        self.say("📝 Let's register your complaint. What type of problem is it?", kb(*rows))

    def ask_room(self, intro: str = "") -> None:
        self.set_state("room")
        if self.user.last_room:
            return self.say(intro + f"Is this for room <b>{esc(self.user.last_room)}</b>?",
                            kb([(f"✅ Yes, {self.user.last_room}", "room:keep"), ("✏️ Different room", "room:new")],
                               [("❌ Cancel", "flow:cancel")]))
        self.say(intro + "Which room are you in? (e.g. <code>A-204</code>)", kb([("❌ Cancel", "flow:cancel")]))

    def ask_description(self) -> None:
        self.set_state("describe")
        self.say("Please describe the problem in a sentence or two. You can also send a photo with a caption.",
                 kb([("❌ Cancel", "flow:cancel")]))

    def ask_media(self) -> None:
        self.set_state("media")
        n = len(self.sess.data.get("attach", []))
        extra = f" ({n}/{self.s.max_attachments} attached)" if n else ""
        self.say(f"📷 Want to add a photo, video or document as evidence?{extra}\nSend it now, or tap Skip.",
                 kb([("➡️ Skip / Done", "media:done")], [("❌ Cancel", "flow:cancel")]))

    def ask_confirm(self) -> None:
        d = self.sess.data
        self.set_state("confirm")
        urgent = "\n🚨 <b>Marked urgent</b>" if d.get("urgent") else ""
        self.say("<b>Please confirm your complaint</b>\n"
                 f"🏷 Category: {CATEGORY_LABELS.get(d['category'], d['category'])}\n"
                 f"🚪 Room: {esc(d['room'])}\n"
                 f"📝 {esc(d['description'])}\n"
                 f"📎 Attachments: {len(d.get('attach', []))}{urgent}",
                 kb([("✅ Submit", "confirm:yes"), ("❌ Cancel", "flow:cancel")],
                    [("🚨 Mark urgent" if not d.get("urgent") else "Remove urgent", "confirm:urgent")]))

    def next_step(self) -> None:
        d = self.sess.data
        if "category" not in d:
            return self.ask_category()
        if "room" not in d:
            return self.ask_room()
        if "description" not in d:
            return self.ask_description()
        if self.sess.state != "media" and not d.get("media_done"):
            return self.ask_media()
        self.ask_confirm()

    def bad_input(self, hint: str) -> None:
        self.sess.fail_count += 1
        if self.sess.fail_count >= 3:
            self.sess.fail_count = 0
            return self.say("I'm having trouble with this form. Would you like to talk to staff instead?",
                            kb([("🙋 Talk to staff", "cmd:human")], [("❌ Cancel", "flow:cancel")]))
        self.say(hint)

    def on_flow_text(self, text: str) -> None:
        st, d = self.sess.state, self.sess.data
        low = text.lower().strip()
        if st == "feedback":
            self.admin_alert(f"💡 <b>Suggestion</b> from {self.who()}:\n{esc(text[:1500])}")
            self.sess.state, self.sess.data = "idle", {}
            return self.say("Thank you! 🙏 I've passed your feedback to the warden's office.")
        if st == "category":
            cat = guess_category(text)
            if not cat:
                return self.bad_input("Please pick one of the categories using the buttons above, or describe the problem.")
            extra = {"category": cat}
            if len(text.split()) >= 4 and "description" not in d:
                extra.update(description=text[:1500], urgent=looks_urgent(text))
            self.set_state("room", **extra)
            return self.next_step()
        if st == "room":
            room = parse_room(text)
            if not room:
                return self.bad_input("Please send your room number, like <code>204</code> or <code>A-204</code>.")
            self.set_state("room", room=room)
            return self.next_step()
        if st == "describe":
            if len(text) < 5:
                return self.bad_input("Please add a bit more detail so the maintenance team knows what to fix.")
            self.set_state("describe", description=text[:1500], urgent=d.get("urgent") or looks_urgent(text))
            return self.next_step()
        if st == "media":
            if low in ("done", "skip", "no", "nope", "none", "that's all", "thats all", "ok", "next"):
                self.set_state("media", media_done=True)
                return self.next_step()
            return self.bad_input("Send a photo/video/document, or tap “Skip / Done”.")
        if st == "confirm":
            if low in ("yes", "y", "submit", "confirm", "ok", "yep", "yeah"):
                return self.submit()
            if low in ("no", "n"):
                return self.cancel()
            return self.say("Tap ✅ Submit to register it, or ❌ Cancel.",
                            kb([("✅ Submit", "confirm:yes"), ("❌ Cancel", "flow:cancel")]))
        self.reset_flow()
        self.on_text(text)

    def submit(self) -> None:
        d = self.sess.data
        if not all(k in d for k in ("category", "room", "description")):
            self.reset_flow()
            return self.say("Something was missing, so please start again with /new.")
        c = Complaint(user_id=self.user.id, chat_id=self.chat_id, reporter_name=self.user.first_name,
                      category=d["category"], room=d["room"], description=d["description"],
                      priority="urgent" if d.get("urgent") else "normal")
        self.db.add(c)
        self.db.flush()
        atts = list(d.get("attach", []))
        if atts:
            self.db.execute(update(Attachment).where(Attachment.id.in_(atts)).values(complaint_id=c.id))
        self.user.last_room = d["room"]
        self.sess.state, self.sess.data, self.sess.fail_count = "idle", {}, 0
        flag = " 🚨 URGENT" if c.priority == "urgent" else ""
        self.admin_alert(
            f"🆕 <b>Complaint #{c.id}</b>{flag}\n👤 {self.who()}\n🚪 Room {esc(c.room)} · 🏷 {c.category}\n📝 {esc(c.description)}\n"
            f"📎 {len(atts)} attachment(s)\n\n<code>/setstatus {c.id} in_progress</code> · <code>/resolve {c.id}</code>")
        for a in self.db.scalars(select(Attachment).where(Attachment.complaint_id == c.id)):
            self.forward_attachment(self.s.admin_chat_id, a, f"Complaint #{c.id}")
        self.say(f"✅ <b>Complaint #{c.id} registered.</b>\nTrack it any time with <code>/status {c.id}</code> — "
                 "I'll message you when the status changes.", MENU_KB)

    def forward_attachment(self, chat_id: int | None, a: Attachment, caption: str) -> None:
        if not chat_id:
            return
        method = {"photo": "sendPhoto", "video": "sendVideo", "voice": "sendVoice", "audio": "sendAudio"}.get(a.kind, "sendDocument")
        field = {"sendPhoto": "photo", "sendVideo": "video", "sendVoice": "voice", "sendAudio": "audio"}.get(method, "document")
        self.res.out.append(Outbound(method, {"chat_id": chat_id, field: a.file_id, "caption": caption}))

    def who(self) -> str:
        u = self.user
        handle = f" (@{esc(u.username)})" if u.username else ""
        return f"{esc(u.first_name or 'Student')}{handle} · id <code>{u.id}</code>"

    # ============================================================== status lookups
    def show_my_complaints(self) -> None:
        rows = self.db.scalars(select(Complaint).where(Complaint.user_id == self.user.id)
                               .order_by(Complaint.id.desc()).limit(8)).all()
        if not rows:
            return self.say("You haven't filed any complaints yet.", kb([("📝 New complaint", "cmd:new")]))
        lines = [f"#{c.id} · {STATUS_LABELS.get(c.status, c.status)} · {c.category} · room {esc(c.room)}" for c in rows]
        self.say("<b>Your recent complaints</b>\n" + "\n".join(lines) + "\n\nSend <code>/status &lt;id&gt;</code> for details.")

    def show_status(self, text: str) -> None:
        m = re.search(r"#?\b(\d{1,7})\b", text or "")
        q = select(Complaint).where(Complaint.user_id == self.user.id)
        c = self.db.scalars(q.where(Complaint.id == int(m.group(1)))).first() if m else \
            self.db.scalars(q.order_by(Complaint.id.desc())).first()
        if not c:
            return self.say("I couldn't find that complaint under your account. Use /mycomplaints to see yours.")
        note = f"\n💬 Staff note: {esc(c.admin_note)}" if c.admin_note else ""
        age = utcnow() - c.created_at
        self.say(f"<b>Complaint #{c.id}</b> — {STATUS_LABELS.get(c.status, c.status)}\n🏷 {c.category} · 🚪 room {esc(c.room)}\n"
                 f"📝 {esc(c.description[:300])}\n🕒 Filed {age.days}d {age.seconds // 3600}h ago{note}", rate=True)

    # ============================================================== media
    def on_media(self, msg: dict) -> None:
        kind, f = None, None
        if msg.get("photo"):
            kind, f = "photo", msg["photo"][-1]
        elif msg.get("document"):
            kind, f = "document", msg["document"]
        elif msg.get("video"):
            kind, f = "video", msg["video"]
        elif msg.get("voice"):
            kind, f = "voice", msg["voice"]
        elif msg.get("audio"):
            kind, f = "audio", msg["audio"]
        else:
            return self.say("Sorry, I can't process that kind of file. Photos, videos, PDFs and voice notes work.")
        self.tag(f"media:{kind}", "flow")
        size, mime = f.get("file_size"), f.get("mime_type")
        if size and size > self.s.max_file_mb * 1024 * 1024:
            return self.say(f"That file is too large (max {self.s.max_file_mb} MB). Could you send a smaller one?")
        if kind == "document" and not (mime or "").startswith(ALLOWED_DOC_MIME):
            return self.say("I can only accept images, videos and PDF documents as evidence.")
        if self.sess.state == "idle":  # photo first: start a complaint silently around it
            self.sess.data = {}
            self.set_state("category", attach=[])
        d = self.sess.data
        if len(d.get("attach", [])) >= self.s.max_attachments:
            return self.say(f"You've already attached {self.s.max_attachments} files, which is the maximum.")
        a = Attachment(chat_id=self.chat_id, kind=kind, file_id=f["file_id"], file_unique_id=f.get("file_unique_id"),
                       mime=mime, size=size)
        self.db.add(a)
        self.db.flush()
        if self.s.download_media:
            self.res.downloads.append((a.id, f["file_id"]))
        self.set_state(self.sess.state, attach=list(d.get("attach", [])) + [a.id])
        caption = (msg.get("caption") or "").strip()
        if caption and len(caption) >= 5 and "description" not in self.sess.data:
            self.set_state(self.sess.state, description=caption[:1500], urgent=looks_urgent(caption))
            cat = guess_category(caption)
            if cat and "category" not in self.sess.data:
                self.set_state(self.sess.state, category=cat)
        self.say("📎 Got it, attachment saved.")
        st = self.sess.state
        if st == "media":
            return self.ask_media()
        if st in ("category", "room", "describe"):
            return self.next_step()
        if st == "confirm":
            return self.ask_confirm()

    # ============================================================== human handover
    def start_handover(self, reason: str, emergency: bool = False, announce: bool = True) -> None:
        existing = self.db.scalars(select(Handover).where(Handover.chat_id == self.chat_id, Handover.status == "open")).first()
        if existing:
            return self.say("You're already connected to staff. Just type your message here. (/endchat to return to the bot)")
        if not self.s.admin_chat_id:
            return self.say("No staff member is available through the bot right now. Please contact the office directly:\n"
                            + esc(self.b.info["warden_contact"]))
        h = Handover(chat_id=self.chat_id, user_id=self.user.id, reason=reason)
        self.db.add(h)
        self.db.flush()
        self.reset_flow()
        self.sess.mode, self.sess.handover_id = "human", h.id
        recent = self.db.scalars(select(Message).where(Message.chat_id == self.chat_id, Message.direction == "in",
                                                       Message.kind != "command").order_by(Message.id.desc()).limit(4)).all()
        ctx = "\n".join(f"• {esc((m.text or '')[:160])}" for m in reversed(recent)) or "–"
        icon = "🚨" if emergency else "🙋"
        self.admin_alert(f"{icon} <b>Handover #{h.id}</b> ({esc(reason)})\n👤 {self.who()}\nRecent messages:\n{ctx}\n\n"
                         f"Reply to this message, or use <code>/reply {h.id} your text</code>. Finish with <code>/close {h.id}</code>.")
        if announce:
            self.say("🙋 I've notified the hostel staff. They'll reply right here in this chat. Please describe what you need.\n"
                     "Type /endchat if you want to go back to the bot.")

    def relay_to_staff(self, msg: dict, text: str, cmd: str | None) -> None:
        hid = self.sess.handover_id
        if cmd == "endchat" or cmd in ("cancel", "stop"):
            self.close_handover(hid, "ended by student")
            self.admin_alert(f"ℹ️ Handover #{hid}: {self.who()} ended the chat.")
            return self.say("Chat with staff ended. I'm back — how can I help?", MENU_KB)
        if cmd == "id":
            return self.on_command(cmd, "")
        head = f"💬 <b>Handover #{hid}</b> · {esc(self.user.first_name or 'Student')} (id {self.user.id})"
        if text and not any(k in msg for k in ("photo", "document", "video", "voice")):
            self.admin_alert(f"{head}\n{esc(text)}")
        else:
            cap = (head + "\n" + (msg.get("caption") or "")).replace("<b>", "").replace("</b>", "")[:1000]
            self.res.out.append(Outbound("copyMessage", {"chat_id": self.s.admin_chat_id, "from_chat_id": self.chat_id,
                                                         "message_id": msg["message_id"], "caption": cap}))
        self.tag("human_relay", "human")

    def close_handover(self, hid: int | None, note: str) -> Handover | None:
        h = self.db.get(Handover, hid) if hid else None
        if h and h.status == "open":
            h.status, h.admin_note, h.resolved_at = "resolved", note, utcnow()
        if h:
            sess = self.db.get(ChatSession, h.chat_id)
            if sess:
                sess.mode, sess.handover_id, sess.state, sess.data, sess.fail_count = "bot", None, "idle", {}, 0
        return h

    def distress(self) -> None:
        """Wellbeing message (self-harm / hopelessness). Caring reply + resources, and staff are alerted (configurable)."""
        alert = self.s.distress_alert_admin and bool(self.s.admin_chat_id)
        tail = ("I'm letting the hostel staff know you may need support, so a real person can reach out. "
                "You can keep writing to them here." if alert else
                "If you'd like to talk to someone at the hostel office, type /human.")
        self.say("💙 I'm really sorry you're going through this, and I'm glad you told me. You don't have to carry it alone.\n\n"
                 "If you might act on these thoughts, or you're in danger right now, please call <b>112</b> or go to someone near you straight away.\n"
                 f"{esc(self.b.info.get('wellbeing_support', self.b.info['emergency_numbers']))}\n\n{tail}")
        if alert:
            self.start_handover("WELLBEING concern", emergency=True, announce=False)

    def emergency(self, text: str) -> None:
        self.say("🚨 <b>This sounds urgent.</b>\n" + esc(self.b.info["emergency_numbers"]) +
                 "\n\nI'm alerting the hostel staff right now. If anyone is in danger, call the numbers above first.")
        if self.s.distress_alert_admin:
            self.start_handover("EMERGENCY: " + text[:40], emergency=True)

    # ============================================================== admin
    def handover_ref(self, msg: dict) -> int | None:
        rt = msg.get("reply_to_message") or {}
        m = re.search(r"Handover #(\d+)", (rt.get("text") or rt.get("caption") or ""))
        return int(m.group(1)) if m else None

    def on_admin(self, msg: dict, cmd: str | None, arg: str) -> None:
        text = (msg.get("text") or "").strip()
        frm = msg.get("from") or {}
        self.chat_id = msg["chat"]["id"]
        reply_h = self.handover_ref(msg)

        def say(t: str, **kw):
            self.send(self.chat_id, t, log_out=False, **kw)

        # plain reply to a handover notice -> relay to the student
        if not cmd and reply_h and text:
            return self.admin_reply(reply_h, text, say)
        if not cmd:
            return
        if cmd == "adminhelp":
            return say("<b>Staff commands</b>\n/tickets [open|in_progress|resolved]\n/ticket &lt;id&gt;\n"
                       "/setstatus &lt;id&gt; &lt;in_progress|resolved|rejected&gt; [note]\n/resolve &lt;id&gt; [note]\n"
                       "/handovers\n/reply &lt;handover id&gt; &lt;text&gt; (or just reply to a handover message)\n"
                       "/close &lt;handover id&gt; [note]\n/stats")
        if cmd == "tickets":
            want = arg.split()[0] if arg else None
            q = select(Complaint).order_by(Complaint.priority.desc(), Complaint.id.desc()).limit(20)
            q = q.where(Complaint.status == want) if want else q.where(Complaint.status.in_(("open", "in_progress")))
            rows = self.db.scalars(q).all()
            if not rows:
                return say("No tickets found.")
            return say("<b>Tickets</b>\n" + "\n".join(
                f"{'🚨' if c.priority == 'urgent' else ''}#{c.id} {STATUS_LABELS.get(c.status)} {c.category} room {esc(c.room)} — {esc(c.description[:50])}"
                for c in rows))
        if cmd == "ticket":
            c = self.db.get(Complaint, int(arg)) if arg.strip().isdigit() else None
            if not c:
                return say("Usage: /ticket &lt;id&gt;")
            n = self.db.scalar(select(func.count()).select_from(Attachment).where(Attachment.complaint_id == c.id))
            say(f"<b>#{c.id}</b> {STATUS_LABELS.get(c.status)} {'🚨' if c.priority == 'urgent' else ''}\n👤 {esc(c.reporter_name or '?')} "
                f"· room {esc(c.room)} · {c.category}\n📝 {esc(c.description)}\n📎 {n} attachment(s)\n"
                f"🕒 {c.created_at:%Y-%m-%d %H:%M} UTC\n💬 {esc(c.admin_note or '–')}")
            for a in self.db.scalars(select(Attachment).where(Attachment.complaint_id == c.id)):
                self.forward_attachment(self.chat_id, a, f"Complaint #{c.id}")
            return
        if cmd in ("setstatus", "resolve"):
            parts = arg.split(None, 2 if cmd == "setstatus" else 1)
            if not parts or not parts[0].isdigit():
                return say("Usage: /setstatus &lt;id&gt; &lt;in_progress|resolved|rejected&gt; [note]  or  /resolve &lt;id&gt; [note]")
            if cmd == "resolve":
                status, note = "resolved", (parts[1] if len(parts) > 1 else "")
            else:
                if len(parts) < 2 or parts[1] not in ("open", "in_progress", "resolved", "rejected"):
                    return say("Status must be one of: open, in_progress, resolved, rejected")
                status, note = parts[1], (parts[2] if len(parts) > 2 else "")
            c = self.db.get(Complaint, int(parts[0]))
            if not c:
                return say(f"Complaint #{parts[0]} not found.")
            c.status, c.updated_at = status, utcnow()
            if note:
                c.admin_note = note
            say(f"✅ #{c.id} → {STATUS_LABELS[status]}")
            if c.chat_id:
                extra = f"\n💬 Staff note: {esc(note)}" if note else ""
                self.send(c.chat_id, f"📣 Update on complaint <b>#{c.id}</b>: {STATUS_LABELS[status]}{extra}", log_out=False)
            return
        if cmd == "handovers":
            rows = self.db.scalars(select(Handover).where(Handover.status == "open").order_by(Handover.id)).all()
            return say("No open handovers." if not rows else "<b>Open handovers</b>\n" + "\n".join(
                f"#{h.id} · {esc(h.reason)} · since {h.created_at:%H:%M} UTC" for h in rows))
        if cmd == "reply":
            hid, _, body = arg.partition(" ")
            if not hid.isdigit() or not body.strip():
                return say("Usage: /reply &lt;handover id&gt; &lt;text&gt;")
            return self.admin_reply(int(hid), body.strip(), say)
        if cmd == "close":
            hid, _, note = arg.partition(" ")
            if not hid.isdigit():
                return say("Usage: /close &lt;handover id&gt; [note]")
            h = self.close_handover(int(hid), note.strip() or "closed by staff")
            if not h:
                return say(f"Handover #{hid} not found.")
            self.send(h.chat_id, "✅ Staff have closed this chat. I'm back — you can keep using the bot, or type /human to reach staff again.",
                      MENU_KB, log_out=False)
            return say(f"Handover #{h.id} closed.")
        if cmd == "stats":
            from .stats import compute_stats, format_stats
            say(format_stats(compute_stats(self.db, self.s)))

    def admin_reply(self, hid: int, body: str, say) -> None:
        h = self.db.get(Handover, hid)
        if not h or h.status != "open":
            return say(f"Handover #{hid} is not open.")
        self.send(h.chat_id, f"👩‍💼 <b>Staff:</b> {esc(body)}", log_out=False)
        self.db.add(Message(chat_id=h.chat_id, direction="out", text=body[:2000], kind="text", intent="human_reply", method="human"))
        say(f"➡️ Sent to student (handover #{hid}).")

    # ============================================================== callbacks & membership
    def answer_cb(self, cq_id: str, text: str | None = None) -> None:
        p = {"callback_query_id": cq_id}
        if text:
            p["text"] = text
        self.res.out.append(Outbound("answerCallbackQuery", p))

    def clear_buttons(self, cq: dict) -> None:
        m = cq.get("message")
        if m:
            self.res.out.append(Outbound("editMessageReplyMarkup", {"chat_id": m["chat"]["id"], "message_id": m["message_id"],
                                                                    "reply_markup": {"inline_keyboard": []}}))

    def on_callback(self, cq: dict) -> None:
        data = cq.get("data", "")
        frm = cq["from"]
        msg = cq.get("message") or {}
        self.chat_id = (msg.get("chat") or {}).get("id") or frm["id"]
        self.user = self.upsert_user(frm, self.chat_id)
        self.sess = self.db.get(ChatSession, self.chat_id) or self.new_session()
        self.log_inbound({"message_id": msg.get("message_id", 0)}, f"[button] {data}", None)
        self.in_msg.kind, self.in_msg.intent, self.in_msg.method = "callback", f"cb:{data.split(':')[0]}", "flow"
        kind, _, val = data.partition(":")
        st = self.sess.state

        if self.sess.mode == "human" and kind != "fb":
            return self.answer_cb(cq["id"], "You're in a chat with staff. Type /endchat to return to the bot.")
        self.answer_cb(cq["id"])  # always stop the button's loading spinner
        if kind == "fb":
            mid, _, v = val.partition(":")
            if mid.lstrip("-").isdigit() and v in ("1", "-1"):
                self.db.add(Feedback(chat_id=self.chat_id, message_id=int(mid), value=int(v)))
                self.clear_buttons(cq)
                self.send(self.chat_id, "Thanks for the feedback! 🙏" if v == "1" else
                          "Thanks — sorry that wasn't helpful. Type /human if you'd like to talk to staff.", log_out=False)
            return
        if kind == "cmd":
            self.clear_buttons(cq)
            if val == "menu_main":
                return self.say(self.help_text(), MENU_KB)
            if val in CMD_TO_INTENT:
                self.tag("cmd:" + val, "flow")
                return self.dispatch(CMD_TO_INTENT[val], "", Prediction(CMD_TO_INTENT[val], 1.0, "rule"))
            return
        if kind == "del":
            self.clear_buttons(cq)
            return self.delete_user_data() if val == "yes" else self.say("Okay, nothing was deleted.")
        if kind == "flow" and val == "cancel":
            self.clear_buttons(cq)
            return self.cancel()

        expired = "That button has expired — type /new to start again."
        if kind == "cat" and st == "category" and val in CATEGORY_LABELS:
            self.clear_buttons(cq)
            self.set_state("room", category=val)
            return self.next_step()
        if kind == "room" and st == "room":
            self.clear_buttons(cq)
            if val == "keep" and self.user.last_room:
                self.set_state("room", room=self.user.last_room)
                return self.next_step()
            return self.say("Okay, send me your room number (e.g. <code>A-204</code>).")
        if kind == "media" and st == "media":
            self.clear_buttons(cq)
            self.set_state("media", media_done=True)
            return self.next_step()
        if kind == "confirm" and st == "confirm":
            if val == "yes":
                self.clear_buttons(cq)
                return self.submit()
            if val == "urgent":
                self.clear_buttons(cq)
                self.set_state("confirm", urgent=not self.sess.data.get("urgent"))
                return self.ask_confirm()
        self.send(self.chat_id, expired, log_out=False)

    def on_member_update(self, mcm: dict) -> None:
        """Telegram tells us when a user blocks or unblocks the bot."""
        chat = mcm.get("chat", {})
        if chat.get("type") != "private":
            return
        status = (mcm.get("new_chat_member") or {}).get("status")
        u = self.db.get(User, mcm["from"]["id"])
        if u is not None:
            u.blocked = status in ("kicked", "left")

    def delete_user_data(self) -> None:
        uid, cid = self.user.id, self.chat_id
        self.db.execute(update(Complaint).where(Complaint.user_id == uid).values(reporter_name=None))
        self.db.execute(delete(Feedback).where(Feedback.chat_id == cid))
        self.db.execute(delete(Message).where(Message.chat_id == cid))
        self.db.execute(delete(Attachment).where(Attachment.chat_id == cid, Attachment.complaint_id.is_(None)))
        self.db.execute(delete(ChatSession).where(ChatSession.chat_id == cid))
        self.db.delete(self.user)
        self.in_msg = None
        self.send(cid, "🗑 Done. Your chat history and profile have been deleted. Send /start any time to begin again.", log_out=False)
