"""Smoke and regression tests for document import, search, auth and URLs."""
import os
from pathlib import Path

os.environ["APP_PASSWORD"] = "safe-test-password-123"
os.environ["APP_SECRET"] = "testing-only-not-used-in-production-234534346"
os.environ.pop("GEMINI_API_KEY", None)

from fastapi.testclient import TestClient
from app import ingest, storage
from app.main import app


def test_categorization():
    assert storage.categorize("Правила ФБР", "простой текст") == "ФБР"
    assert storage.categorize("Правила государственных структур", "простой текст") == "Государственные структуры"
    assert storage.categorize("Уголовный кодекс", "текст") == "Законодательство"


def test_ssrf_url_restrictions():
    assert ingest.validate_forum_url("https://forum.arizona-rp.com/threads/example.123/")
    for address in ["http://forum.arizona-rp.com/", "https://evil.com/", "https://forum.arizona-rp.com.evil.com/", "https://127.0.0.1/"]:
        try:
            ingest.validate_forum_url(address)
            assert False, f"Should be rejected: {address}"
        except ValueError:
            pass


def test_auth_upload_chat_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    storage.initialize()
    with TestClient(app) as client:
        assert client.get("/api/documents").status_code == 401
        assert client.post("/api/session", json={"password": "wrong"}).status_code == 401
        ok = client.post("/api/session", json={"password": "safe-test-password-123"})
        assert ok.status_code == 200
        payload = {
            "title": "Общие правила государственных структур",
            "text": "Стадии применения силы — это порядок последовательных действий по правилам игрового сервера.\n\nВопросы про стадии применения силы описаны в этом разделе.",
            "source_url": "https://forum.arizona-rp.com/threads/example.123/",
            "category": "Автоматически"
        }
        created = client.post("/api/ingest/text", json=payload)
        assert created.status_code == 200, created.text
        record = created.json()["items"][0]
        assert record["category"] == "Государственные структуры"
        assert client.post("/api/ingest/text", json=payload).json()["items"][0]["duplicate"]
        ask = client.post("/api/chat", json={"question": "Что такое стадии применения силы?"})
        assert ask.status_code == 200, ask.text
        assert ask.json()["sources"]
        assert ask.json()["mode"] == "search"
        assert ask.json()["sources"][0]["document_id"] == record["id"]
        assert client.get("/api/stats").json()["documents"] == 1
        assert client.delete(f"/api/documents/{record['id']}").status_code == 200
        assert client.get("/api/stats").json()["documents"] == 0
        empty = client.post("/api/chat", json={"question": "Что такое стадии применения силы?"})
        assert empty.json()["mode"] == "no_results"


def test_multi_file_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    storage.initialize()
    with TestClient(app) as client:
        client.post("/api/session", json={"password": "safe-test-password-123"})
        files = [("files", ("fbi_rules.txt", "Правила ФБР\n\n" + "Это пример правил игрового проекта. " * 5, "text/plain"))]
        response = client.post("/api/ingest/files", files=files, data={"category": "ФБР"})
        assert response.status_code == 200, response.text
        assert len(response.json()["items"]) == 1
        assert response.json()["items"][0]["category"] == "ФБР"
