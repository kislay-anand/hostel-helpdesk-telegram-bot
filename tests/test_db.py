from datetime import timedelta

from app import db


def setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.sqlite3")
    db.init_db()


def test_session_persists_and_is_per_chat(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    db.set_session(1001, "awaiting_location", {"category": "Plumbing"})
    db.set_session(2002, "awaiting_category", {})
    assert db.get_session(1001) == {"state": "awaiting_location", "data": {"category": "Plumbing"}}
    assert db.get_session(2002)["state"] == "awaiting_category"


def test_ticket_owner_is_enforced_and_update_is_idempotent(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    first = db.create_ticket(1001, "student", "Plumbing", "Block A", "Tap is leaking badly", 70001)
    second = db.create_ticket(1001, "student", "Plumbing", "Block A", "Tap is leaking badly", 70001)
    assert first == second
    assert db.get_ticket_for_user(first, 1001) is not None
    assert db.get_ticket_for_user(first, 2002) is None


def test_conversation_session_and_update_deduplication(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    assert db.claim_update(9001) is True
    import pytest
    with pytest.raises(db.UpdateInProgressError):
        db.claim_update(9001)
    db.mark_update_failed(9001)
    assert db.claim_update(9001) is True
    db.mark_update_processed(9001)
    assert db.claim_update(9001) is False
    conversation_id = db.touch_conversation(1001, "student")
    same_conversation = db.touch_conversation(1001, "student")
    assert conversation_id == same_conversation


def test_stats_count_conversations_and_tickets(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    db.touch_conversation(1001, "student")
    db.save_message(1, 1001, "student", "in", "hello")
    db.create_ticket(1001, "student", "Electrical", "Block B", "Fan is not working", 1234)
    stats = db.get_stats()
    assert stats["external_users"] == 1
    assert stats["external_conversations"] == 1
    assert stats["tickets_total"] == 1


def test_delete_user_data_removes_ticket_and_consent(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    db.record_consent(4321)
    db.touch_conversation(4321, "student")
    ticket_id = db.create_ticket(4321, "student", "Cleaning", "Floor 2", "Area needs cleaning")
    assert db.has_consent(4321)
    db.delete_user_data(4321)
    assert not db.has_consent(4321)
    assert db.get_ticket(ticket_id) is None
    assert db.get_session(4321)["state"] == "idle"


def test_conversation_restarts_after_inactivity(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    first = db.touch_conversation(1234, "student")
    with db.connection() as conn:
        conn.execute("UPDATE conversations SET last_activity='2000-01-01T00:00:00+00:00' WHERE id=?", (first,))
    second = db.touch_conversation(1234, "student")
    assert second != first


def test_handover_metric_survives_admin_reply(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    ticket_id = db.create_ticket(1234, "student", "Other", "Block C", "Need help with an issue")
    db.update_ticket_status(ticket_id, "human_requested")
    db.update_ticket_status(ticket_id, "in_progress")
    assert db.get_stats()["human_handover_tickets"] == 1
    assert db.get_ticket(ticket_id)["status"] == "in_progress"


def test_migrates_previous_starter_schema(tmp_path, monkeypatch):
    import sqlite3

    legacy_path = tmp_path / "legacy.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", legacy_path)
    conn = sqlite3.connect(legacy_path)
    conn.executescript(
        """
        CREATE TABLE processed_updates(update_id INTEGER PRIMARY KEY, processed_at TEXT NOT NULL);
        CREATE TABLE sessions(chat_id INTEGER PRIMARY KEY, state TEXT NOT NULL, data_json TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE tickets(ticket_id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, username TEXT, category TEXT NOT NULL, location TEXT NOT NULL, description TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL);
        CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT, update_id INTEGER UNIQUE, chat_id INTEGER NOT NULL, username TEXT, direction TEXT NOT NULL, message_text TEXT, media_type TEXT, is_internal INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
        CREATE TABLE attachments(id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL, chat_id INTEGER NOT NULL, file_id TEXT NOT NULL, media_type TEXT NOT NULL, local_path TEXT NOT NULL, created_at TEXT NOT NULL);
        """
    )
    conn.close()
    db.init_db()
    with db.connection() as conn:
        ticket_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tickets)")}
        update_columns = {row["name"] for row in conn.execute("PRAGMA table_info(processed_updates)")}
        attachment_columns = {row["name"] for row in conn.execute("PRAGMA table_info(attachments)")}
    assert "source_update_id" in ticket_columns
    assert "human_requested_at" in ticket_columns
    assert "status" in update_columns
    assert "file_size" in attachment_columns
