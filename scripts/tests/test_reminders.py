import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TODAY = date.today()
d = lambda n: (TODAY + timedelta(days=n)).isoformat()
NOON = datetime.combine(TODAY, datetime.min.time()).replace(hour=12)
NIGHT = NOON.replace(hour=23)
MORNING = NOON.replace(hour=8)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("POS_DB", str(tmp_path / "pos.db"))
    monkeypatch.setenv("POS_HOME", str(tmp_path))
    for m in [m for m in sys.modules if m == "pos" or m.startswith("pos.")]:
        del sys.modules[m]
    from pos import db, notify, plans, services
    db.init_db()
    sent = []
    monkeypatch.setattr(notify, "_deliver", lambda level, title, detail, *a, **k: sent.append((level, title)) or True)
    return db, notify, plans, services, sent


def seed(db, plans, services):
    from pos import career, life
    with db.session() as con:
        services.add_task(con, "Declarar renta", due=d(-3), prio=1)
        services.add_task(con, "Llamar al banco", due=d(-1))
        life.add_doc(con, "Pasaporte", d(5))
        life.add_doc(con, "Cédula vencida", d(-2))
        life.add_doc(con, "Seguro", d(25))
        life.add_sub(con, "Netflix", 38900, next_date=d(2))
        life.add_person(con, "Ana", birthday=TODAY.strftime("%m-%d"))
        career.add_application(con, "Acme", "Analista", status="interview", next_step="Entrevista", next_step_date=d(1))
        con.execute("INSERT INTO health(metric, value, status, detail) VALUES ('backup_essential_ext_age_days', 80, 'crit', 'sin copia')")
        con.execute("INSERT INTO emails(account, message_id, subject, category, needs_claude, date) VALUES ('a', 'm1', 'Alerta de seguridad: nuevo inicio de sesión', 'security', 1, ?)", (d(0) + "T08:00:00",))
        con.execute("INSERT INTO emails(account, message_id, subject, category, needs_claude, date) VALUES ('a', 'm2', 'Tu contraseña fue cambiada', 'security', 1, ?)", (d(0) + "T09:00:00",))
        con.execute("INSERT INTO emails(account, message_id, subject, category, needs_claude, date) VALUES ('a', 'm3', 'Alerta de seguridad antigua', 'security', 1, ?)", (d(-30) + "T09:00:00",))


def test_rules_create_alerts_from_data_with_levels(env):
    db, notify, plans, services, sent = env
    seed(db, plans, services)
    from pos import reminders
    with db.session() as con:
        rules = {k: lvl for k, lvl, *_ in reminders.evaluate(con, NOON)}
    assert rules["task-overdue:1"] == "CRITICAL"  # prioridad 1
    assert rules["task-overdue:2"] == "IMPORTANT"
    assert rules["doc:1:7"] == "CRITICAL" and rules["doc:2:expired"] == "CRITICAL" and rules["doc:3:30"] == "IMPORTANT"
    assert any(k.startswith("sub:1:") and v == "IMPORTANT" for k, v in rules.items())
    assert any(k.startswith("bday:1:") and v == "IMPORTANT" for k, v in rules.items())
    assert any(k.startswith("app:1:") for k in rules)
    assert rules["health:backup_essential_ext_age_days:crit"] == "CRITICAL" and rules["mail-security:2"] == "CRITICAL" and rules[f"mail-security-routine:{TODAY.isoformat()}"] == "IMPORTANT"
    assert "mail-security:1" not in rules and "mail-security:3" not in rules  # lo rutinario se agrupa; lo viejo no alerta


def test_run_is_idempotent_archives_stale_and_pushes(env):
    db, notify, plans, services, sent = env
    seed(db, plans, services)
    from pos import reminders
    first = reminders.run(NOON)
    assert "nuevas" in first and len(sent) > 0 and all(lvl in ("IMPORTANT", "CRITICAL") for lvl, _ in sent)
    n_sent = len(sent)
    assert ", 0 nuevas," in reminders.run(NOON) and len(sent) == n_sent  # sin repetir
    with db.session() as con:
        services.done_task(con, 2)  # la tarea vencida se resuelve
        con.execute("UPDATE documents SET expires_on=? WHERE id=2", (d(400),))  # documento renovado
    out = reminders.run(NOON)
    with db.session() as con:
        active = {r["key"] for r in con.execute("SELECT key FROM alerts WHERE acknowledged=0")}
    assert "task-overdue:2" not in active and "doc:2:expired" not in active and "task-overdue:1" in active
    assert "archivadas" in out


def test_quiet_hours_hold_important_but_not_critical(env):
    db, notify, plans, services, sent = env
    seed(db, plans, services)
    from pos import reminders
    reminders.run(NIGHT)
    assert sent and all(lvl == "CRITICAL" for lvl, _ in sent)  # de noche solo lo crítico suena
    held = len(sent)
    reminders.run(MORNING)  # por la mañana se liberan las importantes
    assert len(sent) > held and any(lvl == "IMPORTANT" for lvl, _ in sent[held:])


def test_late_plan_and_habit_streak_alerts(env):
    db, notify, plans, services, sent = env
    from pos import life, reminders
    past = (TODAY - timedelta(days=20)).isoformat()
    with db.session() as con:
        pid = plans.create_plan(con, "Curso", "## M\n" + "\n".join(f"- L{i} (video, 30 min)" for i in range(8)), start=past, hours_per_week=14, days="lun,mar,mie,jue,vie,sab,dom")
        con.execute("UPDATE plan_items SET scheduled_date=?", (past,))
        life.add_habit(con, "Caminar")
        for i in (1, 2, 3, 4):
            con.execute("INSERT INTO habit_log VALUES (1, ?)", (d(-i),))
        keys_noon = {k for k, *_ in reminders.evaluate(con, NOON)}
        keys_eve = {k for k, *_ in reminders.evaluate(con, NOON.replace(hour=21))}
    assert any(k.startswith("plan-late:") for k in keys_noon)
    assert not any(k.startswith("habit:") for k in keys_noon) and any(k.startswith("habit:") for k in keys_eve)


def test_many_overdue_tasks_are_grouped(env):
    db, notify, plans, services, sent = env
    from pos import reminders
    with db.session() as con:
        for i in range(8):
            services.add_task(con, f"Vieja {i}", due=d(-5))
        rules = reminders.evaluate(con, NOON)
    assert [r[0] for r in rules if r[0].startswith("task")] == [f"tasks-overdue:{TODAY.isoformat()}"]


def test_insistent_tag_nags_every_day_until_the_task_is_done(env):
    db, notify, plans, services, sent = env
    from pos import reminders
    with db.session() as con:
        tid = services.add_task(con, "Descargar lo de la maestría", due=d(2), tags="insistir")
        keys = {k for k, *_ in reminders.evaluate(con, NOON)}
        assert f"nag:{tid}:{TODAY.isoformat()}" in keys                                    # hoy ya avisa, sin esperar a que venza
        keys_tomorrow = {k for k, *_ in reminders.evaluate(con, NOON + timedelta(days=1))}
        assert f"nag:{tid}:{(TODAY + timedelta(days=1)).isoformat()}" in keys_tomorrow     # y mañana otra vez
        services.done_task(con, tid)
        assert not any(k.startswith("nag:") for k, *_ in reminders.evaluate(con, NOON))    # al hacerla, deja de insistir
