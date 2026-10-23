"""Fechas de familia y pareja (avisos a 30 y 15 días), festivos de Colombia e importación de Coursera."""
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("POS_DB", str(tmp_path / "pos.db"))
    monkeypatch.setenv("POS_HOME", str(tmp_path))
    for m in [m for m in sys.modules if m == "pos" or m.startswith("pos.")]:
        del sys.modules[m]
    from pos import coursera, dates, db, life, reminders
    db.init_db()
    return db, dates, life, reminders, coursera


def test_occurrence_rules(env):
    _, dates, *_ = env
    assert dates.occurrence("2:6:5", 2027) == date(2027, 5, 9)     # Día de la Madre: 2.º domingo de mayo
    assert dates.occurrence("3:6:6", 2027) == date(2027, 6, 20)    # Día del Padre
    assert dates.occurrence("3:5:9", 2026) == date(2026, 9, 19)    # Amor y Amistad: 3.er sábado de septiembre
    assert dates.next_occurrence("09-21", date(2026, 9, 30)) == date(2027, 9, 21)
    assert dates.rrule("3:5:9") == "RRULE:FREQ=YEARLY;BYMONTH=9;BYDAY=3SA"


def test_colombian_holidays_match_google_calendar(env):
    _, dates, *_ = env
    h27 = {d.isoformat() for d in dates.holidays(2027)}
    assert h27 == {"2027-01-01", "2027-01-11", "2027-03-22", "2027-03-25", "2027-03-26", "2027-05-01", "2027-05-10", "2027-05-31", "2027-06-07",
                   "2027-07-05", "2027-07-20", "2027-08-07", "2027-08-16", "2027-10-18", "2027-11-01", "2027-11-15", "2027-12-08", "2027-12-25"}
    h26 = {d.isoformat() for d in dates.holidays(2026) if d >= date(2026, 9, 30)}
    assert h26 == {"2026-10-12", "2026-11-02", "2026-11-16", "2026-12-08", "2026-12-25"}


def test_close_circle_alerts_at_30_15_and_day(env):
    db, dates, life, reminders, _ = env
    with db.session() as con:
        pid = life.add_person(con, "Mamá", "mamá", "12-28", circle="familia")
        life.add_person(con, "Amigo", "", "12-28", circle="otros")
        dates.add_special(con, "Aniversario", "07-15", pid)
        for day, left in ((date(2026, 11, 28), 30), (date(2026, 12, 13), 15), (date(2026, 12, 28), 0)):
            rules = reminders.evaluate(con, datetime.combine(day, datetime.min.time()).replace(hour=9))
            close = [r for r in rules if r[0].startswith("close:")]
            assert len(close) == 1 and close[0][1] == "IMPORTANT" and str(left) in close[0][0].split(":")[-1], (day, close)
        # una persona de «otros» NO avisa a 30 días
        rules = reminders.evaluate(con, datetime(2026, 11, 28, 9))
        assert not [r for r in rules if r[0].startswith("bday:")]
        # un día cualquiera no avisa
        assert not [r for r in reminders.evaluate(con, datetime(2026, 12, 1, 9)) if r[0].startswith("close:")]


def test_coursera_ingest_moves_and_drops(env):
    db, _, _, _, coursera = env
    ev = {"id": "ABC", "summary": "Assignment Due -- Simulación", "start": "2026-10-12T04:00:00Z", "end": "2026-10-12T04:59:00Z",
          "description": "Simulación -- Week 9\nhttps://www.coursera.org/learn/x\n\nYou have 2 items due\n - Caso 9\n - Quiz 9\n\nStay on track"}
    ses = {"id": "SES", "summary": "Sesiones Sincrónicas Simulación - Simulación", "start": "2026-10-12T22:00:00Z", "end": "2026-10-12T23:20:00Z",
           "description": "Join live event\nhttps://universidad-edu.zoom.us/j/1"}
    with db.session() as con:
        s = coursera.ingest(con, [ev, ses], today=date(2026, 10, 1))
        assert s["created"] == 2
        t = con.execute("SELECT title, due_date FROM tasks WHERE source='coursera' ORDER BY id").fetchall()
        assert t[0]["due_date"] == "2026-10-11" and "Caso 9; Quiz 9" in t[0]["title"]        # 23:59 del domingo, hora de Bogotá
        assert t[1]["due_date"] == "2026-10-12" and "17:00" in t[1]["title"]
        assert coursera.ingest(con, [ev, ses], today=date(2026, 10, 1))["unchanged"] == 2     # idempotente
        moved = dict(ev, end="2026-10-19T04:59:00Z")
        s = coursera.ingest(con, [moved, ses], today=date(2026, 10, 1))
        assert s["moved"] == 1 and con.execute("SELECT due_date FROM tasks WHERE title LIKE '%Caso 9%'").fetchone()[0] == "2026-10-18"
        s = coursera.ingest(con, [moved], today=date(2026, 10, 1), window_from="2026-10-01")   # la sesión desapareció del calendario
        assert s["dropped"] == 1 and con.execute("SELECT status FROM tasks WHERE title LIKE '%sesión%'").fetchone()[0] == "dropped"


def test_coursera_proposal_needs_user_ok(env):
    db, _, _, _, coursera = env
    ev = {"id": "ABC", "summary": "Assignment Due -- Simulación", "start": "2026-10-12T04:00:00Z", "end": "2026-10-12T04:59:00Z",
          "description": "Simulación -- Week 9\n\nYou have 1 item due\n - Caso 9\n"}
    with db.session() as con:
        assert coursera.is_due(con, date(2026, 10, 1))                                      # nunca revisado
        s = coursera.propose(con, [ev], "2026-10-01", date(2026, 10, 1))
        assert s["created"] == 1 and con.execute("SELECT COUNT(*) FROM tasks WHERE source='coursera'").fetchone()[0] == 0  # NO aplicó nada
        assert con.execute("SELECT COUNT(*) FROM alerts WHERE source='coursera'").fetchone()[0] == 1                       # avisó al usuario
        assert not coursera.is_due(con, date(2026, 10, 20)) and coursera.is_due(con, date(2026, 11, 2))                     # espaciada: 30 días
        assert "1 nuevas" in coursera.apply_proposal(con, date(2026, 10, 1))
        assert con.execute("SELECT COUNT(*) FROM tasks WHERE source='coursera'").fetchone()[0] == 1
        # segunda revisión: el calendario movió la fecha → propone, y «no» deja todo como estaba
        moved = dict(ev, end="2026-10-19T04:59:00Z")
        coursera.propose(con, [moved], "2026-10-01", date(2026, 11, 2))
        coursera.discard_proposal(con)
        assert con.execute("SELECT due_date FROM tasks WHERE source='coursera'").fetchone()[0] == "2026-10-11"
        # sin cambios: no molesta
        assert coursera.propose(con, [ev], "2026-10-01", date(2026, 12, 5))["changes"] == []


def test_payment_task_alerts_the_day_before(env):
    db, _, _, reminders, _ = env
    with db.session() as con:
        con.execute("INSERT INTO tasks(title, due_date, priority, tags) VALUES ('Pagar Nu', '2026-10-18', 3, 'pago,nu')")
        con.execute("INSERT INTO tasks(title, due_date, priority) VALUES ('Tarea normal', '2026-10-18', 3)")
        rules = reminders.evaluate(con, datetime(2026, 10, 17, 9))
        soon = [r for r in rules if r[0].startswith("task-soon:")]
        assert len(soon) == 1 and "mañana" in soon[0][2] and soon[0][1] == "IMPORTANT"
        assert [r for r in reminders.evaluate(con, datetime(2026, 10, 18, 9)) if r[0].startswith("task-soon:")][0][2].startswith("Vence HOY")
        assert not [r for r in reminders.evaluate(con, datetime(2026, 10, 15, 9)) if r[0].startswith("task-soon:")]
