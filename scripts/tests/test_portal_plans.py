import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "portal"))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("POS_DB", str(tmp_path / "pos.db"))
    monkeypatch.setenv("POS_HOME", str(tmp_path))
    for m in [m for m in sys.modules if m in ("pos", "app") or m.startswith("pos.")]:
        del sys.modules[m]
    from pos import db, media, notes
    db.init_db()
    monkeypatch.setattr(media, "MEDIA_HOME", tmp_path / "media")
    monkeypatch.setattr(notes, "VAULT", tmp_path / "kb")
    import app as portal
    return TestClient(portal.app, base_url="http://127.0.0.1:8765", follow_redirects=False)


def test_plans_flow_gantt_ics_and_ask(client):
    outline = "Semana 1\nVideo: Intro · 10 min\nQuiz 1 · 15 min\nSemana 2\nLab: Práctica · 45 min\n"
    r = client.post("/plans/create", data={"title": "Curso Coursera", "outline": outline, "hours_per_week": "5", "days": ["0", "1", "2", "3", "4", "5", "6"],
                                           "tags": "coursera"})
    assert r.status_code == 303 and r.headers["location"].startswith("/plans/1")
    page = client.get("/plans/1").text
    assert "Curso Coursera" in page and "Semana 2" in page and "g-bar" in page and "#coursera" in page
    assert "built-in method" not in page and "built-in method" not in client.get("/plans").text  # p.items chocaba con dict.items
    assert "1/3" in page or "0/3" in page
    assert "g-bar" in client.get("/plans").text
    assert "Curso Coursera" in client.get("/").text  # HOY muestra lo programado para hoy (todos los días son de trabajo)
    assert client.post("/plans/items/1/toggle", data={"back": "/plans/1"}).status_code == 303
    assert client.post("/plans/1/reschedule").status_code == 303
    ics = client.get("/plans/1/calendar.ics")
    assert ics.status_code == 200 and b"BEGIN:VCALENDAR" in ics.content
    assert client.post("/ask/add", data={"question": "¿Qué repaso del quiz?", "scope": "plan:1"}).status_code == 303
    assert "repaso del quiz" in client.get("/ask").text
    assert client.get("/plans/999").status_code == 404


def test_invalid_outline_is_reported_not_crashing(client):
    r = client.post("/plans/create", data={"title": "Vacío", "outline": "   ", "hours_per_week": "5"})
    assert r.status_code == 303 and "plans" in r.headers["location"]


def test_ai_answers_and_notes_cannot_inject_html(client):
    from pos import ask, db, notes
    with db.session() as con:
        qid = ask.add_question(con, "hola")
        ask.save_answer(con, qid, "<script>alert(1)</script> <img src=x onerror=alert(1)> **negrita**", "claude")
    body = client.get("/ask").text
    assert "<script>alert" not in body and "<img src=x" not in body and "<strong>negrita</strong>" in body
    p = notes.create_note("Trampa", "inbox", "", "<script>alert(2)</script>")
    notes.index_notes()
    view = client.get("/knowledge/view?path=inbox/" + p.name).text
    assert "<script>alert(2)" not in view


def test_task_tags_filter(client):
    client.post("/tasks/add", data={"title": "Estudiar clustering", "tags": "#Coursera datos"})
    client.post("/tasks/add", data={"title": "Pagar arriendo", "tags": "casa"})
    only = client.get("/tasks?tag=coursera").text
    assert "Estudiar clustering" in only and "Pagar arriendo" not in only
    assert "#casa" in client.get("/tasks").text
