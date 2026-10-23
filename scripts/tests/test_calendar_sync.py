import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TODAY = date.today()
d = lambda n: (TODAY + timedelta(days=n)).isoformat()


@pytest.fixture()
def db_(tmp_path, monkeypatch):
    monkeypatch.setenv("POS_DB", str(tmp_path / "pos.db"))
    monkeypatch.setenv("POS_HOME", str(tmp_path))
    for m in [m for m in sys.modules if m == "pos" or m.startswith("pos.")]:
        del sys.modules[m]
    from pos import db
    db.init_db()
    return db


def test_wanted_pending_mark_and_stale(db_):
    from pos import calendar_sync as cal, career, life, services
    with db_.session() as con:
        life.add_doc(con, "Pasaporte", d(40))
        life.add_doc(con, "Vencido hace tiempo", d(-30))
        life.add_doc(con, "Muy lejano", d(400))
        life.add_sub(con, "Netflix", 38900, next_date=d(5))
        life.add_person(con, "Ana", birthday=(TODAY + timedelta(days=10)).strftime("%m-%d"))
        career.add_application(con, "Acme", "Analista", status="interview", next_step="Entrevista", next_step_date=d(3))
        services.add_task(con, "Renta", due=d(7), prio=1)
        services.add_task(con, "Tarea normal", due=d(7), prio=3)  # las normales no van al calendario
        keys = {w["key"].split(":")[0] for w in cal.wanted(con)}
        assert keys == {"doc", "sub", "bday", "app", "task"} and len(cal.wanted(con)) == 5
        birthday = next(w for w in cal.wanted(con) if w["key"].startswith("bday"))
        assert birthday["recurrence"] == "RRULE:FREQ=YEARLY" and birthday["all_day"]
        app_ev = next(w for w in cal.wanted(con) if w["key"].startswith("app"))
        assert app_ev["all_day"] is False and app_ev["start_time"] == "09:00"
        doc_ev = next(w for w in cal.wanted(con) if w["key"].startswith("doc"))
        assert doc_ev["reminders"] == [43200 - 540, 7 * 1440 - 540, 1440 - 540]  # 30, 7 y 1 día antes a las 9:00
        # marcar como creado → deja de estar pendiente
        cal_key = doc_ev["key"]
        con.execute("INSERT INTO calendar_sync(key, event_id, title, date) VALUES (?,?,?,?)", (cal_key, "evt1", "x", doc_ev["date"]))
        assert cal_key not in {p["key"] for p in cal.pending(con)} and len(cal.pending(con)) == 4
        # la fecha del documento cambia: el evento viejo queda obsoleto y el nuevo es pendiente
        con.execute("UPDATE documents SET expires_on=? WHERE name='Pasaporte'", (d(60),))
        assert [s["key"] for s in cal.stale(con)] == [cal_key]
        assert any(p["date"] == d(60) for p in cal.pending(con))


def test_cli_roundtrip(db_):
    from typer.testing import CliRunner
    from pos.cli import app
    from pos import life
    r = CliRunner()
    with db_.session() as con:
        life.add_doc(con, "SOAT", d(20))
    out = r.invoke(app, ["calendar", "pending"]).stdout
    assert "SOAT" in out and "1 pendientes" in out
    key = out.split("[")[1].split("]")[0]
    assert r.invoke(app, ["calendar", "mark", key, "evt9", "--title", "SOAT", "--date", d(20)]).exit_code == 0
    assert "0 pendientes" in r.invoke(app, ["calendar", "pending"]).stdout
    assert r.invoke(app, ["calendar", "forget", key]).exit_code == 0
    assert "1 pendientes" in r.invoke(app, ["calendar", "pending"]).stdout
