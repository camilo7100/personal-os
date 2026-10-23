import sys
from pathlib import Path

import keyring
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "portal"))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    store = {}
    monkeypatch.setattr(keyring, "get_password", lambda s, u: store.get((s, u)))
    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: store.__setitem__((s, u), p))
    monkeypatch.setattr(keyring, "delete_password", lambda s, u: store.pop((s, u), None))
    monkeypatch.setenv("POS_DB", str(tmp_path / "pos.db"))
    monkeypatch.setenv("POS_HOME", str(tmp_path))
    for m in [m for m in sys.modules if m in ("pos", "app") or m.startswith("pos.")]:
        del sys.modules[m]
    from pos import db, media, notes
    db.init_db()
    monkeypatch.setattr(media, "MEDIA_HOME", tmp_path / "media")
    monkeypatch.setattr(notes, "VAULT", tmp_path / "kb")
    import app as portal
    from pos import setup as onboarding
    onboarding._cache["t"] = 0
    return portal, onboarding, TestClient(portal.app, base_url="http://127.0.0.1:8765", follow_redirects=False), store


def test_steps_reflect_real_state_and_count_only_required(env):
    portal, onboarding, client, store = env
    from pos import portal_security as sec, services
    steps = {s["key"]: s for s in onboarding.steps(force=True)}
    assert not steps["pin"]["done"] and not steps["plan"]["done"] and steps["editors"]["optional"] and steps["remote"]["optional"]
    before = onboarding.pending()
    sec.set_pin("clave-larga-1")
    from pos import db, plans
    with db.session() as con:
        plans.create_plan(con, "Curso", "## M\n- L1 (video, 10 min)")
    onboarding._cache["t"] = 0
    steps = {s["key"]: s for s in onboarding.steps(force=True)}
    assert steps["pin"]["done"] and steps["plan"]["done"] and onboarding.pending() == before - 2


def test_start_page_actions_and_manual_flag(env, monkeypatch):
    portal, onboarding, client, store = env
    page = client.get("/start").text
    assert "Primeros pasos" in page and "Guardar la clave de los backups" in page and "opcional" in page
    launched = []
    monkeypatch.setattr(onboarding.subprocess, "Popen", lambda *a, **k: launched.append(a))
    monkeypatch.setattr(onboarding.os, "startfile", lambda p: launched.append(p), raising=False)
    monkeypatch.setattr(onboarding, "_audit", lambda *a, **k: None)
    assert client.post("/start/install-editors").status_code == 303 and any("RunAs" in str(x) for x in launched)
    assert client.post("/start/open-backup").status_code == 303 and any("Backup-Esencial" in str(x) for x in launched)
    assert client.post("/start/open-mail").status_code == 303  # sin Thunderbird en el entorno de prueba: mensaje, no error
    assert client.post("/start/mark/backupkey").status_code == 303
    onboarding._cache["t"] = 0
    assert {s["key"]: s for s in onboarding.steps(force=True)}["backupkey"]["done"]
    assert client.post("/start/mark/inventado").status_code == 303  # ignorado sin romper


def test_telegram_setup_flow_in_portal(env, monkeypatch):
    portal, onboarding, client, store = env
    from pos import telegram
    calls = {"getMe": {"ok": True, "result": {"username": "mi_pos_bot"}}, "getUpdates": {"result": [{"update_id": 7, "message": {"chat": {"id": 555, "first_name": "Alex"}}}]}}
    sent = []
    monkeypatch.setattr(telegram, "api", lambda method, params=None, **k: calls.get(method, {"ok": True, "result": []}))
    monkeypatch.setattr(telegram, "send", lambda t: sent.append(t) or True)
    assert client.post("/start/telegram/verify").status_code == 303  # sin token: mensaje de error, no falla
    assert not telegram.configured()
    assert client.post("/start/telegram/token", data={"token": "123:ABC"}).status_code == 303
    assert "mi_pos_bot" in client.get("/start/telegram").text
    assert "123:ABC" not in client.get("/start/telegram").text  # el token nunca se muestra
    assert client.post("/start/telegram/verify").status_code == 303 and telegram.configured()
    assert store[("pos", "telegram-chat")] == "555" and sent and "conectado" in sent[0]


def test_start_stays_hidden_from_remote_menu_but_opens(env, tmp_path):
    portal, onboarding, client, store = env
    from pos import portal_security as sec
    sec.set_pin("clave-larga-1")
    sec.CONFIG.parent.mkdir(exist_ok=True)
    sec.CONFIG.write_text('{"allowed_hosts": ["pc.tail.ts.net"]}')
    r = TestClient(portal.app, base_url="https://pc.tail.ts.net", follow_redirects=False)
    r.post("/login", data={"pin": "clave-larga-1", "next": "/"})
    assert r.get("/start").status_code == 200  # decisión del usuario: todo se abre en todos lados
    assert "Primeros pasos" not in r.get("/").text  # pero el menú remoto no muestra el asistente de instalación


def test_telegram_commands_act_on_alerts_and_tasks(env):
    portal, onboarding, client, store = env
    from datetime import date, timedelta
    from pos import db, notify, services, telegram
    with db.session() as con:
        t = services.add_task(con, "Pagar arriendo", due="2000-01-01")
    notify.alert("IMPORTANT", "Prueba de alerta", key="k1")
    assert "Prueba de alerta" in telegram.handle_command("/alertas")
    assert "archivada" in telegram.handle_command("/ok 1") and "Sin alertas" in telegram.handle_command("/alertas")
    assert "movida al" in telegram.handle_command(f"/pospon {t} mañana")
    with db.session() as con:
        assert con.execute("SELECT due_date FROM tasks WHERE id=?", (t,)).fetchone()[0] == (date.today() + timedelta(days=1)).isoformat()
    assert "Uso:" in telegram.handle_command(f"/pospon {t} nunca-jamas")
    assert "Hecha" in telegram.handle_command(f"/hecho {t}") and "No encontré" in telegram.handle_command(f"/hecho {t}")
    assert "/alertas" in telegram.handle_command("/ayuda")


def test_claude_answers_and_briefings_push_to_phone(env, monkeypatch, tmp_path):
    portal, onboarding, client, store = env
    from pos import ask, briefing, db
    pushed = []
    monkeypatch.delenv("POS_NO_NOTIFY")  # aquí sí queremos ejercer el envío (Telegram simulado)
    monkeypatch.setattr("pos.telegram.send", lambda t, buttons=None: pushed.append(t) or True)
    monkeypatch.setattr(briefing, "OUT", tmp_path / "briefings")
    with db.session() as con:
        qid = ask.add_question(con, "¿Voy bien?")
        ask.save_answer(con, qid, "Vas al día.", "claude")
    with db.session() as con:
        ask.save_answer(con, qid, "Respuesta local.", "local")
    assert any("Respuesta a tu pregunta" in p and "Vas al día" in p for p in pushed)
    assert not any("Respuesta local" in p for p in pushed)
    briefing.write("morning")
    assert any("Buen día" in p for p in pushed)

def test_telegram_buttons_tarea_and_checklist(env):
    portal, onboarding, client, store = env
    from pos import db, services, telegram, tgfmt
    assert tgfmt.buttons("task-soon:7:2026-10-02", 3) == [[("✅ Hecho", "done:7"), ("⏰ Mañana", "snooze:7")], [("👌 Entendido", "ok:3")]]
    assert tgfmt.buttons("coursera-proposal:2026-10-01", None)[0][0][1] == "coursera:si"
    out = telegram.handle_command("/tarea comprar leche mañana")
    assert out.startswith("➕ Tarea #") and "leche" in out and "📅" in out
    with db.session() as con:
        tid = con.execute("SELECT id FROM tasks WHERE title='Comprar leche'").fetchone()[0]
    assert "☐" in telegram.handle_command("/tareas") and "/hecho" in telegram.handle_command("/tareas")
    assert "movida" in telegram.handle_callback(f"snooze:{tid}") and "Hecha" in telegram.handle_callback(f"done:{tid}")
    assert "No encontré" in telegram.handle_callback(f"done:{tid}")
    assert "próximos 7" in telegram.handle_command("/semana").lower() or "despejada" in telegram.handle_command("/semana")
