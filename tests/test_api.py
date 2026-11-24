"""בדיקות לשרת: ולידציה של קלט ודפים. בלי קריאה ל-OpenAI."""
from fastapi.testclient import TestClient

from app.api import app

client = TestClient(app)


def test_index_page():
    r = client.get("/")
    assert r.status_code == 200
    assert "צ'אט הוראות" in r.text


def test_empty_question_rejected():
    r = client.post("/api/chat", json={"question": "   "})
    assert r.status_code == 400


def test_too_long_question_rejected():
    r = client.post("/api/chat", json={"question": "א" * 5000})
    assert r.status_code == 400


def test_missing_document_404():
    assert client.get("/doc/no-such-doc").status_code == 404
