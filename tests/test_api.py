from fastapi.testclient import TestClient

from app import db
from app import main


def test_health_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "api.sqlite3")
    with TestClient(main.app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_webhook_rejects_wrong_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "webhook.sqlite3")
    monkeypatch.setattr(main, "WEBHOOK_SECRET", "a-very-long-test-secret")
    with TestClient(main.app) as client:
        response = client.post(
            "/telegram/webhook",
            json={"update_id": 123, "message": {}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        assert response.status_code == 403


def test_webhook_processes_update_once(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "once.sqlite3")
    monkeypatch.setattr(main, "WEBHOOK_SECRET", "another-long-test-secret")
    processed = []

    async def fake_handle_message(message, update_id):
        processed.append(update_id)

    monkeypatch.setattr(main, "handle_message", fake_handle_message)
    payload = {
        "update_id": 555001,
        "message": {"chat": {"id": 123, "type": "private"}, "text": "/start"},
    }
    headers = {"X-Telegram-Bot-Api-Secret-Token": "another-long-test-secret"}
    with TestClient(main.app) as client:
        first = client.post("/telegram/webhook", json=payload, headers=headers)
        second = client.post("/telegram/webhook", json=payload, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json().get("duplicate") is True
    assert processed == [555001]
