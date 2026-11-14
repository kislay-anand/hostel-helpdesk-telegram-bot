"""Usage statistics for the evaluation report: genuine users/conversations, intents, latency, handovers, ratings."""
from __future__ import annotations

from collections import Counter
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings
from .db import Complaint, Feedback, Handover, Message, User, utcnow


def compute_stats(db: Session, s: Settings, min_user_messages: int = 2) -> dict:
    """A *conversation* is a run of messages in one chat with no gap longer than `conversation_gap_min`.
    A *genuine* conversation belongs to a non-team user and contains at least `min_user_messages` of their messages."""
    team = {u for (u,) in db.execute(select(User.id).where(User.is_team.is_(True)))} | s.team_ids | s.admin_ids
    gap = timedelta(minutes=s.conversation_gap_min)

    rows = db.execute(select(Message.chat_id, Message.user_id, Message.direction, Message.ts, Message.intent,
                             Message.method, Message.latency_ms, Message.kind)
                      .order_by(Message.chat_id, Message.ts, Message.id)).all()
    chat_owner = {c: u for c, u, d, *_ in rows if d == "in" and u}
    conversations: list[dict] = []
    cur: dict | None = None
    for chat_id, user_id, direction, ts, intent, method, lat, kind in rows:
        if cur is None or cur["chat_id"] != chat_id or ts - cur["last"] > gap:
            cur = {"chat_id": chat_id, "user": chat_owner.get(chat_id), "start": ts, "last": ts, "in": 0, "out": 0}
            conversations.append(cur)
        cur["last"] = ts
        cur["in" if direction == "in" else "out"] += 1

    genuine = [c for c in conversations if c["user"] and c["user"] not in team and c["in"] >= min_user_messages]
    genuine_chats = {c["chat_id"] for c in genuine}

    inbound = [r for r in rows if r[2] == "in" and r[0] in genuine_chats and r[7] != "callback"]
    intents = Counter(r[4] for r in inbound if r[4] and not r[4].startswith(("command:", "flow:")))
    methods = Counter(r[5] for r in inbound if r[5])
    lat = sorted(r[6] for r in inbound if r[6] is not None)
    n_unknown = intents.get("unknown", 0)
    n_nlu = sum(v for k, v in intents.items() if not k.startswith(("cb:", "cmd:", "media:", "human_relay")))

    complaints = db.execute(select(Complaint.status, Complaint.category, Complaint.priority, Complaint.user_id)).all()
    complaints = [c for c in complaints if c[3] not in team]
    handovers = db.execute(select(Handover.reason, Handover.status, Handover.user_id)).all()
    handovers = [h for h in handovers if h[2] not in team]
    ups = db.scalar(select(func.count()).select_from(Feedback).where(Feedback.value > 0)) or 0
    downs = db.scalar(select(func.count()).select_from(Feedback).where(Feedback.value < 0)) or 0
    days: Counter = Counter()
    for c in genuine:
        days[c["start"].strftime("%Y-%m-%d")] += 1

    # a conversation "resolved by the bot" = no handover / emergency in that chat, no 'unknown' dead-end left unanswered
    escalated_chats = {h_chat for (h_chat,) in db.execute(select(Handover.chat_id))}
    bot_resolved = sum(1 for c in genuine if c["chat_id"] not in escalated_chats)

    def pct(q: float) -> float | None:
        return round(lat[min(len(lat) - 1, int(q * len(lat)))], 1) if lat else None

    return {
        "generated_at": utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "genuine_users": len({c["user"] for c in genuine}),
        "genuine_conversations": len(genuine),
        "all_conversations_incl_team": len(conversations),
        "user_messages": len(inbound),
        "bot_messages": sum(c["out"] for c in genuine),
        "avg_user_messages_per_conversation": round(sum(c["in"] for c in genuine) / len(genuine), 2) if genuine else 0,
        "complaints_total": len(complaints),
        "complaints_by_status": dict(Counter(c[0] for c in complaints)),
        "complaints_by_category": dict(Counter(c[1] for c in complaints)),
        "urgent_complaints": sum(1 for c in complaints if c[2] == "urgent"),
        "handovers_total": len(handovers),
        "handovers_by_reason": dict(Counter(h[0].split(":")[0] for h in handovers)),
        "bot_resolved_conversations": bot_resolved,
        "bot_resolution_rate": round(bot_resolved / len(genuine), 3) if genuine else None,
        "fallback_rate": round(n_unknown / n_nlu, 3) if n_nlu else None,
        "intent_counts": dict(intents.most_common()),
        "nlu_method_counts": dict(methods),
        "bot_processing_latency_ms": {"p50": pct(0.5), "p95": pct(0.95)},
        "ratings": {"thumbs_up": ups, "thumbs_down": downs},
        "conversations_per_day": dict(sorted(days.items())),
    }


def format_stats(st: dict) -> str:
    lat = st["bot_processing_latency_ms"]
    top = ", ".join(f"{k} ({v})" for k, v in list(st["intent_counts"].items())[:5]) or "–"
    return (f"<b>📊 Bot statistics</b> ({st['generated_at']})\n"
            f"Genuine users: <b>{st['genuine_users']}</b> · genuine conversations: <b>{st['genuine_conversations']}</b>\n"
            f"User messages: {st['user_messages']} · avg per conversation: {st['avg_user_messages_per_conversation']}\n"
            f"Complaints: {st['complaints_total']} {st['complaints_by_status']}\n"
            f"Handovers: {st['handovers_total']} · bot-resolved: {st['bot_resolution_rate']}\n"
            f"Fallback rate: {st['fallback_rate']} · latency p50/p95: {lat['p50']}/{lat['p95']} ms\n"
            f"Ratings 👍{st['ratings']['thumbs_up']} 👎{st['ratings']['thumbs_down']}\n"
            f"Top intents: {top}")
