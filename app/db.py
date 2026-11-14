"""SQLite persistence. All SQL uses parameters; ticket lookups enforce ownership."""
from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from app.config import (
    ADMIN_CHAT_ID,
    CONVERSATION_GAP_MINUTES,
    DB_PATH,
    TEAM_CHAT_IDS,
    UPDATE_PROCESSING_TTL_SECONDS,
)


def now_dt() -> datetime:
    return datetime.now(timezone.utc)


def now_iso(value: datetime | None = None) -> str:
    return (value or now_dt()).astimezone(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS processed_updates (
                update_id INTEGER PRIMARY KEY,
                processed_at TEXT,
                status TEXT NOT NULL DEFAULT 'processed',
                claimed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                username TEXT,
                started_at TEXT NOT NULL,
                last_activity TEXT NOT NULL,
                incoming_messages INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_conversations_chat_last
                ON conversations(chat_id, last_activity DESC);
            CREATE TABLE IF NOT EXISTS consents (
                chat_id INTEGER PRIMARY KEY,
                consented_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                chat_id INTEGER PRIMARY KEY,
                state TEXT NOT NULL,
                data_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                username TEXT,
                category TEXT NOT NULL,
                location TEXT NOT NULL,
                description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                source_update_id INTEGER,
                human_requested_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tickets_chat_created
                ON tickets(chat_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                update_id INTEGER UNIQUE,
                conversation_id INTEGER,
                chat_id INTEGER NOT NULL,
                username TEXT,
                direction TEXT NOT NULL,
                message_text TEXT,
                media_type TEXT,
                is_internal INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id)
            );
            CREATE INDEX IF NOT EXISTS idx_messages_chat_created
                ON messages(chat_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                file_id TEXT NOT NULL,
                media_type TEXT NOT NULL,
                local_path TEXT NOT NULL,
                original_name TEXT,
                file_size INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(ticket_id, file_id)
            );
            """
        )
        # Lightweight migration from the earlier starter repository.
        _ensure_column(conn, "processed_updates", "status", "TEXT NOT NULL DEFAULT 'processed'")
        _ensure_column(conn, "processed_updates", "claimed_at", "TEXT")
        _ensure_column(conn, "tickets", "source_update_id", "INTEGER")
        _ensure_column(conn, "tickets", "human_requested_at", "TEXT")
        _ensure_column(conn, "messages", "conversation_id", "INTEGER")
        _ensure_column(conn, "attachments", "original_name", "TEXT")
        _ensure_column(conn, "attachments", "file_size", "INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_source_update "
            "ON tickets(source_update_id) WHERE source_update_id IS NOT NULL"
        )


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    names = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in names:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


class UpdateInProgressError(Exception):
    """Raised when another webhook request currently owns this update."""


def claim_update(update_id: int) -> bool:
    """Atomically claim an update; False means already processed."""
    current = now_dt()
    current_text = now_iso(current)
    stale_before = now_iso(current - timedelta(seconds=UPDATE_PROCESSING_TTL_SECONDS))
    with connection() as conn:
        row = conn.execute(
            "SELECT status, claimed_at FROM processed_updates WHERE update_id=?", (update_id,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO processed_updates(update_id,status,claimed_at) VALUES(?, 'processing', ?)",
                (update_id, current_text),
            )
            return True
        if row["status"] == "processed":
            return False
        claimed_at = row["claimed_at"] or ""
        if row["status"] == "processing" and claimed_at >= stale_before:
            raise UpdateInProgressError(f"Telegram update {update_id} is already being processed")
        conn.execute(
            "UPDATE processed_updates SET status='processing', claimed_at=?, processed_at=NULL WHERE update_id=?",
            (current_text, update_id),
        )
        return True


def mark_update_processed(update_id: int) -> None:
    with connection() as conn:
        conn.execute(
            "UPDATE processed_updates SET status='processed', processed_at=?, claimed_at=NULL WHERE update_id=?",
            (now_iso(), update_id),
        )


def mark_update_failed(update_id: int) -> None:
    with connection() as conn:
        conn.execute(
            "UPDATE processed_updates SET status='failed', claimed_at=NULL WHERE update_id=?",
            (update_id,),
        )


def touch_conversation(chat_id: int, username: str | None = None) -> int:
    """Start a new conversation after an inactivity gap; otherwise update the active one."""
    now = now_dt()
    now_text = now_iso(now)
    threshold = now_iso(now - timedelta(minutes=CONVERSATION_GAP_MINUTES))
    with connection() as conn:
        row = conn.execute(
            "SELECT id, last_activity FROM conversations WHERE chat_id=? ORDER BY id DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
        if row and row["last_activity"] >= threshold:
            conn.execute(
                "UPDATE conversations SET last_activity=?, username=COALESCE(?, username), "
                "incoming_messages=incoming_messages+1 WHERE id=?",
                (now_text, username, row["id"]),
            )
            return int(row["id"])
        cur = conn.execute(
            "INSERT INTO conversations(chat_id,username,started_at,last_activity,incoming_messages) "
            "VALUES(?,?,?,?,1)",
            (chat_id, username, now_text, now_text),
        )
        return int(cur.lastrowid)


def save_message(
    update_id: int | None,
    chat_id: int,
    username: str | None,
    direction: str,
    message_text: str | None,
    media_type: str | None = None,
    is_internal: bool = False,
    conversation_id: int | None = None,
) -> None:
    with connection() as conn:
        if conversation_id is None:
            row = conn.execute(
                "SELECT id FROM conversations WHERE chat_id=? ORDER BY id DESC LIMIT 1", (chat_id,)
            ).fetchone()
            conversation_id = int(row["id"]) if row else None
        conn.execute(
            """INSERT OR IGNORE INTO messages
               (update_id,conversation_id,chat_id,username,direction,message_text,media_type,is_internal,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                update_id, conversation_id, chat_id, username, direction,
                (message_text or "")[:4000], media_type, int(is_internal), now_iso()
            ),
        )



def has_consent(chat_id: int) -> bool:
    with connection() as conn:
        row = conn.execute("SELECT 1 FROM consents WHERE chat_id=?", (chat_id,)).fetchone()
    return row is not None


def record_consent(chat_id: int) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO consents(chat_id,consented_at) VALUES(?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET consented_at=excluded.consented_at",
            (chat_id, now_iso()),
        )


def delete_user_data(chat_id: int) -> list[str]:
    """Delete user-visible records and return saved attachment paths for file removal."""
    with connection() as conn:
        paths = [row["local_path"] for row in conn.execute(
            "SELECT local_path FROM attachments WHERE chat_id=?", (chat_id,)
        ).fetchall()]
        conn.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
        conn.execute("DELETE FROM attachments WHERE chat_id=?", (chat_id,))
        conn.execute("DELETE FROM tickets WHERE chat_id=?", (chat_id,))
        conn.execute("DELETE FROM conversations WHERE chat_id=?", (chat_id,))
        conn.execute("DELETE FROM sessions WHERE chat_id=?", (chat_id,))
        conn.execute("DELETE FROM consents WHERE chat_id=?", (chat_id,))
    return paths


def get_session(chat_id: int) -> dict:
    with connection() as conn:
        row = conn.execute(
            "SELECT state,data_json FROM sessions WHERE chat_id=?", (chat_id,)
        ).fetchone()
    if not row:
        return {"state": "idle", "data": {}}
    try:
        data = json.loads(row["data_json"])
    except (TypeError, json.JSONDecodeError):
        data = {}
    return {"state": row["state"], "data": data}


def set_session(chat_id: int, state: str, data: dict | None = None) -> None:
    with connection() as conn:
        conn.execute(
            """INSERT INTO sessions(chat_id,state,data_json,updated_at) VALUES(?,?,?,?)
               ON CONFLICT(chat_id) DO UPDATE SET state=excluded.state,
               data_json=excluded.data_json,updated_at=excluded.updated_at""",
            (chat_id, state, json.dumps(data or {}, ensure_ascii=False), now_iso()),
        )


def create_ticket(
    chat_id: int,
    username: str | None,
    category: str,
    location: str,
    description: str,
    source_update_id: int | None = None,
) -> str:
    """Create one ticket per source update to make webhook retries idempotent."""
    with connection() as conn:
        if source_update_id is not None:
            existing = conn.execute(
                "SELECT ticket_id FROM tickets WHERE source_update_id=?", (source_update_id,)
            ).fetchone()
            if existing:
                return str(existing["ticket_id"])
        for _ in range(5):
            ticket_id = f"HST-{now_dt().strftime('%y%m%d')}-{secrets.token_hex(3).upper()}"
            try:
                conn.execute(
                    """INSERT INTO tickets
                       (ticket_id,chat_id,username,category,location,description,status,created_at,source_update_id)
                       VALUES(?,?,?,?,?,?,'open',?,?)""",
                    (ticket_id, chat_id, username, category, location, description, now_iso(), source_update_id),
                )
                return ticket_id
            except sqlite3.IntegrityError:
                if source_update_id is not None:
                    existing = conn.execute(
                        "SELECT ticket_id FROM tickets WHERE source_update_id=?", (source_update_id,)
                    ).fetchone()
                    if existing:
                        return str(existing["ticket_id"])
        raise RuntimeError("Could not generate a unique ticket identifier")


def get_ticket_for_user(ticket_id: str, chat_id: int):
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM tickets WHERE ticket_id=? AND chat_id=?", (ticket_id.strip().upper(), chat_id)
        ).fetchone()


def get_ticket(ticket_id: str):
    with connection() as conn:
        return conn.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id.strip().upper(),)).fetchone()


def get_latest_active_ticket_for_user(chat_id: int):
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM tickets WHERE chat_id=? AND status!='closed' ORDER BY created_at DESC LIMIT 1",
            (chat_id,),
        ).fetchone()


def update_ticket_status(ticket_id: str, status: str) -> bool:
    allowed = {"open", "human_requested", "in_progress", "closed"}
    if status not in allowed:
        raise ValueError("Unsupported ticket status")
    with connection() as conn:
        if status == "human_requested":
            cur = conn.execute(
                "UPDATE tickets SET status=?, human_requested_at=COALESCE(human_requested_at, ?) WHERE ticket_id=?",
                (status, now_iso(), ticket_id.strip().upper()),
            )
        else:
            cur = conn.execute(
                "UPDATE tickets SET status=? WHERE ticket_id=?", (status, ticket_id.strip().upper())
            )
        return cur.rowcount > 0


def add_attachment(
    ticket_id: str,
    chat_id: int,
    file_id: str,
    media_type: str,
    local_path: str,
    original_name: str | None = None,
    file_size: int = 0,
) -> None:
    with connection() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO attachments
               (ticket_id,chat_id,file_id,media_type,local_path,original_name,file_size,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (ticket_id, chat_id, file_id, media_type, local_path, original_name, file_size, now_iso()),
        )


def get_stats() -> dict:
    excluded = set(TEAM_CHAT_IDS)
    if ADMIN_CHAT_ID is not None:
        excluded.add(ADMIN_CHAT_ID)
    ids = sorted(excluded)
    if ids:
        marks = ",".join("?" for _ in ids)
        msg_filter = f"chat_id NOT IN ({marks}) AND is_internal=0"
        ticket_filter = f"chat_id NOT IN ({marks})"
        conv_filter = f"chat_id NOT IN ({marks})"
        params = tuple(ids)
    else:
        msg_filter = "is_internal=0"
        ticket_filter = "1=1"
        conv_filter = "1=1"
        params = ()
    with connection() as conn:
        incoming_messages = conn.execute(
            f"SELECT COUNT(*) AS n FROM messages WHERE direction='in' AND {msg_filter}", params
        ).fetchone()["n"]
        external_users = conn.execute(
            f"SELECT COUNT(DISTINCT chat_id) AS n FROM messages WHERE direction='in' AND {msg_filter}", params
        ).fetchone()["n"]
        external_conversations = conn.execute(
            f"SELECT COUNT(*) AS n FROM conversations WHERE {conv_filter}", params
        ).fetchone()["n"]
        tickets_total = conn.execute(
            f"SELECT COUNT(*) AS n FROM tickets WHERE {ticket_filter}", params
        ).fetchone()["n"]
        open_tickets = conn.execute(
            f"SELECT COUNT(*) AS n FROM tickets WHERE {ticket_filter} AND status IN ('open','human_requested','in_progress')",
            params,
        ).fetchone()["n"]
        handovers = conn.execute(
            f"SELECT COUNT(*) AS n FROM tickets WHERE {ticket_filter} AND human_requested_at IS NOT NULL", params
        ).fetchone()["n"]
        attachments = conn.execute(
            f"SELECT COUNT(*) AS n FROM attachments WHERE {ticket_filter}", params
        ).fetchone()["n"]
        category_rows = conn.execute(
            f"SELECT category,COUNT(*) AS n FROM tickets WHERE {ticket_filter} GROUP BY category ORDER BY n DESC",
            params,
        ).fetchall()
    return {
        "external_users": int(external_users),
        "external_conversations": int(external_conversations),
        "incoming_messages": int(incoming_messages),
        "tickets_total": int(tickets_total),
        "tickets_open_or_escalated": int(open_tickets),
        "human_handover_tickets": int(handovers),
        "attachments_saved": int(attachments),
        "tickets_by_category": {row["category"]: row["n"] for row in category_rows},
        "conversation_definition": f"One conversation is a chat session separated from the prior message by more than {CONVERSATION_GAP_MINUTES} minutes of inactivity.",
    }
