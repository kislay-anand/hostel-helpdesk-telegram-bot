import asyncio
from pathlib import Path

from app import db, bot


def test_user_can_create_ticket_and_only_owner_can_view_it(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "bot-flow.sqlite3")
    monkeypatch.setattr(bot, "ADMIN_CHAT_ID", None)
    monkeypatch.setattr(bot, "UPLOAD_DIR", tmp_path / "uploads")
    db.init_db()
    sent = []

    async def fake_send_message(chat_id, text, reply_markup=None):
        sent.append((chat_id, text, reply_markup))
        return {"message_id": len(sent)}

    monkeypatch.setattr(bot, "send_message", fake_send_message)

    async def flow():
        user = {"chat": {"id": 111, "type": "private"}, "from": {"username": "student"}}
        await bot.handle_message({**user, "text": "/start"}, 100)
        await bot.handle_message({**user, "text": "I AGREE"}, 101)
        await bot.handle_message({**user, "text": "New complaint"}, 102)
        await bot.handle_message({**user, "text": "Plumbing"}, 103)
        await bot.handle_message({**user, "text": "Block B, Room 214"}, 104)
        await bot.handle_message({**user, "text": "The tap is leaking continuously near the sink."}, 105)

    asyncio.run(flow())
    tickets = db.get_stats()["tickets_total"]
    assert tickets == 1
    with db.connection() as conn:
        row = conn.execute("SELECT ticket_id FROM tickets WHERE chat_id=111").fetchone()
    ticket_id = row["ticket_id"]

    async def track():
        owner = {"chat": {"id": 111, "type": "private"}, "from": {"username": "student"}}
        stranger = {"chat": {"id": 222, "type": "private"}, "from": {"username": "other"}}
        await bot.handle_message({**owner, "text": "/status"}, 106)
        await bot.handle_message({**owner, "text": ticket_id}, 107)
        await bot.handle_message({**stranger, "text": "/start"}, 108)
        await bot.handle_message({**stranger, "text": "I AGREE"}, 109)
        await bot.handle_message({**stranger, "text": "/status"}, 110)
        await bot.handle_message({**stranger, "text": ticket_id}, 111)

    asyncio.run(track())
    assert any(ticket_id in text for chat_id, text, _ in sent if chat_id == 111)
    assert any("No ticket with that ID belongs" in text for chat_id, text, _ in sent if chat_id == 222)


def test_attachment_is_saved_and_forwarded_to_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "media.sqlite3")
    monkeypatch.setattr(bot, "ADMIN_CHAT_ID", 999)
    monkeypatch.setattr(bot, "UPLOAD_DIR", tmp_path / "uploads")
    db.init_db()
    ticket_id = db.create_ticket(111, "student", "Plumbing", "Block B", "Leak in sink")
    sent = []
    forwarded = []

    async def fake_send_message(chat_id, text, reply_markup=None):
        sent.append((chat_id, text))
        return {"message_id": len(sent)}

    async def fake_download(file_id, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"test")
        return "photos/evidence.jpg", 4

    async def fake_send_media(chat_id, path, media_type, caption=""):
        forwarded.append((chat_id, Path(path).read_bytes(), media_type, caption))

    monkeypatch.setattr(bot, "send_message", fake_send_message)
    monkeypatch.setattr(bot, "download_telegram_file", fake_download)
    monkeypatch.setattr(bot, "send_media", fake_send_media)

    async def run():
        message = {
            "chat": {"id": 111, "type": "private"},
            "from": {"username": "student"},
            "photo": [{"file_id": "photo-id", "file_size": 4}],
        }
        await bot._save_and_forward_media(
            message, 111, "student", {"state": "awaiting_media", "data": {"ticket_id": ticket_id}}
        )

    asyncio.run(run())
    with db.connection() as conn:
        attachment = conn.execute("SELECT * FROM attachments WHERE ticket_id=?", (ticket_id,)).fetchone()
    assert attachment is not None
    assert Path(attachment["local_path"]).exists()
    assert forwarded and forwarded[0][0] == 999 and forwarded[0][1] == b"test"


def test_human_handover_admin_reply_and_close(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "handover.sqlite3")
    monkeypatch.setattr(bot, "ADMIN_CHAT_ID", 999)
    monkeypatch.setattr(bot, "UPLOAD_DIR", tmp_path / "uploads")
    db.init_db()
    db.record_consent(111)
    sent = []

    async def fake_send_message(chat_id, text, reply_markup=None):
        sent.append((chat_id, text))
        return {"message_id": len(sent)}

    monkeypatch.setattr(bot, "send_message", fake_send_message)

    async def run():
        user = {"chat": {"id": 111, "type": "private"}, "from": {"username": "student"}}
        await bot.handle_message({**user, "text": "/human"}, 700)
        with db.connection() as conn:
            row = conn.execute("SELECT ticket_id FROM tickets WHERE chat_id=111").fetchone()
        ticket_id = row["ticket_id"]
        admin = {"chat": {"id": 999, "type": "private"}, "from": {"username": "admin"}}
        await bot.handle_message({**admin, "text": f"/reply {ticket_id} We have received your request."}, 701)
        assert db.get_ticket(ticket_id)["status"] == "in_progress"
        await bot.handle_message({**admin, "text": f"/close {ticket_id}"}, 702)
        assert db.get_ticket(ticket_id)["status"] == "closed"

    asyncio.run(run())
    assert any(chat_id == 999 and "HUMAN HANDOVER REQUEST" in text for chat_id, text in sent)
    assert any(chat_id == 111 and "We have received your request" in text for chat_id, text in sent)
