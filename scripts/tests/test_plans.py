import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

COURSERA_RAW = """Google Data Analytics
Semana 1
Introducción al análisis de datos
Video: Bienvenido al curso · 4 min
Lectura: Cómo funciona el curso · 10 min
Quiz de práctica · 15 min
Show all
Semana 2
Video: Ciclo de vida de los datos (1 hr 5 min)
Lab: Hojas de cálculo · 45 min
Assignment: Proyecto final 1 h
"""

OUTLINE_MD = """# Curso de prueba
## Módulo 1: Fundamentos
- Introducción (video, 12 min)
- Quiz de repaso (quiz, 15 min)
## Módulo 2: Práctica
- Ejercicio guiado (practica, 90 min)
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("POS_DB", str(tmp_path / "pos.db"))
    monkeypatch.setenv("POS_HOME", str(tmp_path))
    for m in [m for m in sys.modules if m == "pos" or m.startswith("pos.")]:
        del sys.modules[m]
    from pos import db, notes
    db.init_db()
    monkeypatch.setattr(notes, "VAULT", tmp_path / "kb")
    return db


def test_parse_raw_coursera_text():
    from pos import plans
    st = plans.parse_outline(COURSERA_RAW)
    assert [s["title"] for s in st] == ["Semana 1", "Semana 2"]
    s1 = st[0]["items"]
    assert [i["minutes"] for i in s1] == [30, 4, 10, 15] and s1[0]["title"] == "Introducción al análisis de datos"
    assert [i["kind"] for i in s1][1:] == ["video", "lectura", "quiz"]
    assert [i["minutes"] for i in st[1]["items"]] == [65, 45, 60]
    assert st[1]["items"][2]["kind"] == "entrega" and "Show all" not in str(st)


def test_parse_markdown_outline():
    from pos import plans
    st = plans.parse_outline(OUTLINE_MD)
    assert len(st) == 2 and st[0]["items"][1] == {"title": "Quiz de repaso", "kind": "quiz", "minutes": 15, "url": None}
    assert st[1]["items"][0]["minutes"] == 90


def test_schedule_respects_hours_days_and_reschedule(env):
    from pos import plans
    start = date.today() + timedelta(days=(7 - date.today().weekday()))  # próximo lunes
    with env.session() as con:
        # 3 h/semana en 3 días = 60 min por día
        pid = plans.create_plan(con, "Curso", OUTLINE_MD, start=start.isoformat(), hours_per_week=3, days="lun,mie,vie", tags="#Coursera IA")
        items = con.execute("SELECT title, est_minutes, scheduled_date FROM plan_items ORDER BY id").fetchall()
        dates = [date.fromisoformat(i["scheduled_date"]) for i in items]
        assert all(d.weekday() in (0, 2, 4) for d in dates) and dates == sorted(dates)
        per_day = {}
        for i in items:
            per_day[i["scheduled_date"]] = per_day.get(i["scheduled_date"], 0) + i["est_minutes"]
        assert per_day[start.isoformat()] == 12 + 15  # los dos primeros caben juntos (27 ≤ 60)
        assert con.execute("SELECT tags FROM plans WHERE id=?", (pid,)).fetchone()[0] == "coursera,ia"
        assert con.execute("SELECT end_date FROM plans WHERE id=?", (pid,)).fetchone()[0] == items[-1]["scheduled_date"]
        # hacer el primero y reprogramar desde hoy no mueve lo hecho
        first = con.execute("SELECT id FROM plan_items ORDER BY id LIMIT 1").fetchone()[0]
        plans.toggle_item(con, first)
        plans.schedule(con, pid, (start + timedelta(days=14)).isoformat())
        assert con.execute("SELECT MIN(scheduled_date) FROM plan_items WHERE status='todo'").fetchone()[0] == (start + timedelta(days=14)).isoformat()
        assert con.execute("SELECT scheduled_date FROM plan_items WHERE id=?", (first,)).fetchone()[0] == start.isoformat()


def test_progress_state_today_gantt_and_ics(env):
    from pos import plans
    past = (date.today() - timedelta(days=10)).isoformat()
    with env.session() as con:
        pid = plans.create_plan(con, "Curso", OUTLINE_MD, start=past, hours_per_week=10, days="lun,mar,mie,jue,vie,sab,dom")
        con.execute("UPDATE plan_items SET scheduled_date=?", (past,))  # todo quedó atrasado
        pr = plans.progress(con, pid)
        assert pr["late"] == 3 and pr["state"] == "casi al día" and pr["percent"] == 0
        assert len(plans.plan_today(con)) == 3
        for iid in [r[0] for r in con.execute("SELECT id FROM plan_items")]:
            plans.toggle_item(con, iid)
        assert plans.progress(con, pid)["percent"] == 100
        assert con.execute("SELECT status FROM plans WHERE id=?", (pid,)).fetchone()[0] == "done"
        g = plans.gantt([{"label": "A", "start": past, "end": date.today().isoformat(), "percent": 100}])
        assert g["rows"][0]["width"] > 0 and g["today"] is not None and any(w["show"] for w in g["weeks"])
        plans.schedule(con, pid, date.today().isoformat())  # nada pendiente: no rompe
        con.execute("UPDATE plan_items SET status='todo', scheduled_date=? WHERE id=1", (date.today().isoformat(),))
        ics = plans.to_ics(con, pid)
    assert ics.startswith("BEGIN:VCALENDAR") and "SUMMARY:📚 Curso" in ics and ics.count("BEGIN:VEVENT") == 1 and f"UID:plan{pid}-" in ics


def test_ask_retrieves_context_and_stores_answers(env):
    from pos import ask, notes, plans
    with env.session() as con:
        pid = plans.create_plan(con, "Curso de datos", "## M1\n- Regresión lineal (video, 20 min)\n- Clustering k-means (video, 25 min)")
    notes.create_note("Clustering", "estudio", "ml", "k-means agrupa puntos por distancia al centroide")
    notes.index_notes()
    with env.session() as con:
        qid = ask.add_question(con, "¿Qué debo repasar sobre clustering?", f"plan:{pid}")
        ctx = con.execute("SELECT context FROM questions WHERE id=?", (qid,)).fetchone()[0]
        assert "k-means" in ctx and "Curso de datos" in ctx
        ask.save_answer(con, qid, "Repasa **centroides**.", "claude")
        r = con.execute("SELECT status, answered_by FROM questions WHERE id=?", (qid,)).fetchone()
        assert (r["status"], r["answered_by"]) == ("answered", "claude")


def test_tags_and_cloud(env):
    from pos import services
    with env.session() as con:
        services.add_task(con, "Estudiar", tags="#Coursera, Datos coursera")
        services.add_project(con, "Maestría", tags="estudio")
        cloud = {c["tag"]: c["n"] for c in services.tag_cloud(con)}
    assert cloud == {"coursera": 1, "datos": 1, "estudio": 1}


def test_telegram_phone_commands(env, monkeypatch):
    from pos import plans, services, telegram
    with env.session() as con:
        services.add_task(con, "Llamar al contador", due="2000-01-01")
        plans.create_plan(con, "Curso", OUTLINE_MD, start=date.today().isoformat(), hours_per_week=10, days="lun,mar,mie,jue,vie,sab,dom")
    assert "Llamar al contador" in telegram.handle_command("/tareas")
    assert "Curso" in telegram.handle_command("/plan") and "VENCIDAS" not in telegram.handle_command("/plan")
    assert "Buen día" in telegram.handle_command("/hoy") and "☐" in telegram.handle_command("/hoy")
    assert telegram.handle_command("/pregunta ¿qué sigue?").startswith("Pregunta #1")
    assert telegram.handle_command("comprar leche") is None
    assert "/hoy" in telegram.handle_command("/ayuda")
