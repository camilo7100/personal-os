"""Gestor de tareas: captura en lenguaje natural, edición, bloques de tiempo y subtareas."""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TODAY = date(2026, 10, 1)  # jueves


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("POS_DB", str(tmp_path / "pos.db"))
    monkeypatch.setenv("POS_HOME", str(tmp_path))
    for m in [m for m in sys.modules if m == "pos" or m.startswith("pos.")]:
        del sys.modules[m]
    from pos import db, quick, services
    db.init_db()
    return db, quick, services


def q(quick, text, projects=("Maestría MIAD (Uniandes)", "Casa")):
    return quick.parse_quick(text, list(projects), TODAY)


def test_parse_full_sentence(env):
    _, quick, _ = env
    r = q(quick, "llamar al contador mañana 3pm ~30m p1 #Maestría @pago")
    assert r["title"] == "Llamar al contador" and r["due"] == "2026-10-02" and r["time"] == "15:00"
    assert r["est_minutes"] == 30 and r["priority"] == 1 and r["project"] == "Maestría MIAD (Uniandes)" and r["tags"] == ["pago"]


def test_parse_dates_times_and_recurrence(env):
    _, quick, _ = env
    assert q(quick, "revisar informe viernes a las 10:30 por 1h")["due"] == "2026-10-02"
    r = q(quick, "revisar informe viernes a las 10:30 por 1h")
    assert r["time"] == "10:30" and r["est_minutes"] == 60 and r["title"] == "Revisar informe"
    assert q(quick, "pagar tarjeta el 18")["due"] == "2026-10-18"
    assert q(quick, "viaje 15 de diciembre")["due"] == "2026-12-15"
    assert q(quick, "cita en 3 días")["due"] == "2026-10-04"
    assert q(quick, "cita dentista hoy a las 9 de la noche")["time"] == "21:00"
    r = q(quick, "regar plantas cada lunes")
    assert r["recurrence"] == "weekly" and r["due"] == "2026-10-05" and r["title"] == "Regar plantas"
    assert q(quick, "pagar arriendo cada mes el 5")["recurrence"] == "monthly"
    assert q(quick, "caminar todos los días 7am")["recurrence"] == "daily"
    assert q(quick, "aprender italiano algún día")["someday"] is True
    assert q(quick, "comprar leche")["due"] is None                                    # sin fecha: queda sin fecha
    assert q(quick, "mañana de la mañana reunión 8am")["time"] == "08:00"
    assert q(quick, "informe #Proyecto-raro @x")["tags"] == ["proyecto-raro", "x"]    # #desconocido se vuelve etiqueta


def test_add_quick_update_schedule_subtasks_and_stats(env):
    db, _, services = env
    with db.session() as con:
        services.add_project(con, "Casa")
        tid, parsed = services.add_quick(con, "arreglar el grifo viernes 4pm ~45m p2 #casa @hogar")
        row = services.task_row(con, tid)
        assert row["project"] == "Casa" and row["start_time"] == "16:00" and row["est_minutes"] == 45 and row["priority"] == 2 and row["tags"] == "hogar"
        assert services.update_task(con, tid, title="Arreglar grifo de la cocina", priority=1, project="")
        assert services.task_row(con, tid)["project"] is None and services.task_row(con, tid)["priority"] == 1
        with pytest.raises(ValueError):
            services.update_task(con, tid, status_x="no")
        assert services.schedule_task(con, tid, "2026-10-03", "09:00", 30)
        assert (services.task_row(con, tid)["scheduled_date"], services.task_row(con, tid)["start_time"]) == ("2026-10-03", "09:00")
        s1 = services.add_subtask(con, tid, "Comprar empaque")
        services.add_subtask(con, tid, "Cerrar llave")
        assert [s["title"] for s in services.subtasks(con, tid)] == ["Comprar empaque", "Cerrar llave"]
        services.done_task(con, s1)
        services.log_focus(con, tid, 25)
        assert services.task_row(con, tid)["actual_minutes"] == 25
        assert sum(d["n"] for d in services.done_by_day(con, 7)) == 1


def test_recurrence_keeps_tags_time_and_duration(env):
    db, _, services = env
    with db.session() as con:
        tid, _ = services.add_quick(con, "pagar nu el 18 a las 9am ~10m @pago cada mes", "portal")
        nxt = services.done_task(con, tid)[1]
        r = services.task_row(con, nxt)
        assert r["tags"] == "pago" and r["start_time"] == "09:00" and r["est_minutes"] == 10 and r["recurrence"] == "monthly"


def test_agenda_unifies_everything_and_planner_respects_calendar(env):
    db, _, services = env
    from datetime import datetime, timedelta
    from pos import agenda, life, planner, dates
    day = date(2026, 10, 5)  # lunes
    with db.session() as con:
        life.add_person(con, "Mamá", "mamá", "10-06", circle="familia")
        con.execute("INSERT INTO documents(name, expires_on) VALUES ('Pasaporte', '2026-10-07')")
        agenda.cache_events(con, [
            {"id": "e1", "summary": "Reunión", "start": {"dateTime": "2026-10-05T10:00:00-05:00"}, "end": {"dateTime": "2026-10-05T11:30:00-05:00"}},
            {"id": "e2", "summary": "Viaje", "start": {"date": "2026-10-06"}, "end": {"date": "2026-10-08"}}], "primary", "2026-10-01", "2026-12-31")
        t1, _ = services.add_quick(con, "informe final lunes ~60m p1")
        t2, _ = services.add_quick(con, "llamar a Juan lunes ~15m")
        con.execute("UPDATE tasks SET due_date=?, scheduled_date=? WHERE id IN (?,?)", (day.isoformat(),) * 2 + (t1, t2))
        rows = agenda.items(con, day, day + timedelta(days=4))
        kinds = {r["kind"] for r in rows}
        assert {"evento", "familia", "tarea", "documento", "festivo"} <= kinds | {"festivo"}            # el 12 de octubre queda fuera del rango: sin festivo
        assert [r["date"] for r in rows if r["kind"] == "evento" and r["title"] == "Viaje"] == ["2026-10-06", "2026-10-07"]  # evento de varios días
        # el planificador evita la reunión y el almuerzo, y respeta la capacidad
        s = planner.suggest(con, day, datetime(2026, 10, 5, 7, 0))
        spans = [(p["start"], p["end"]) for p in s["plan"]]
        assert spans and all(not (a < "11:40" and b > "10:00") for a, b in spans) and all(not (a < "13:30" and b > "12:30") for a, b in spans)
        assert planner.apply(con, s["plan"], day) == len(s["plan"])
        l = planner.load(con, day)
        assert l["count"] == 2 and l["state"] == "ok" and l["capacity"] == 300


def test_planner_warns_about_overload_and_suggests_rebalance(env):
    db, _, services = env
    from datetime import timedelta
    from pos import planner
    day = date(2026, 10, 7)  # miércoles
    with db.session() as con:
        for i in range(11):
            services.add_task(con, f"Tarea {i}", due=day.isoformat(), prio=3)
        con.execute("UPDATE tasks SET est_minutes=45")
        l = planner.load(con, day)
        assert l["state"] == "over" and "Demasiado" in l["advice"] and l["count"] == 11
        # con vencimiento lejano se pueden mover al día más libre
        con.execute("UPDATE tasks SET due_date=?, scheduled_date=? WHERE id<=4", ((day + timedelta(days=3)).isoformat(), day.isoformat()))
        moves = planner.rebalance(con, day - timedelta(days=day.weekday()))
        assert moves and all(m["from"] != m["to"] for m in moves)


def test_time_blocks_alert_15_min_before_and_sync_to_google(env):
    db, _, services = env
    from datetime import datetime
    from pos import calendar_sync, reminders
    with db.session() as con:
        tid, _ = services.add_quick(con, "reunión con el contador 2026-10-05 10am ~60m p1")
        con.execute("UPDATE tasks SET scheduled_date='2026-10-05' WHERE id=?", (tid,))
        # 20 minutos antes: todavía no; 10 minutos antes: avisa (una sola vez, con clave estable); después de empezar: ya no
        assert not [r for r in reminders.evaluate(con, datetime(2026, 10, 5, 9, 30)) if r[0].startswith("block:")]
        soon = [r for r in reminders.evaluate(con, datetime(2026, 10, 5, 9, 50)) if r[0].startswith("block:")]
        assert len(soon) == 1 and soon[0][1] == "IMPORTANT" and "10:00" in soon[0][2] and "10 min" in soon[0][3]
        assert not [r for r in reminders.evaluate(con, datetime(2026, 10, 5, 10, 30)) if r[0].startswith("block:")]
        # el bloque se sincroniza con Google Calendar como evento con hora, duración y aviso a 10 min
        ev = [w for w in calendar_sync.wanted(con, date(2026, 10, 1)) if w["key"].startswith("block:")]
        assert ev and ev[0]["all_day"] is False and ev[0]["start_time"] == "10:00" and ev[0]["duration_min"] == 60 and ev[0]["reminders"] == [10]
        # si lo mueves en el portal, la clave cambia: el evento viejo queda «obsoleto» y se crea el nuevo
        con.execute("INSERT INTO calendar_sync(key, event_id, title, date) VALUES (?,?,?,?)", (ev[0]["key"], "g1", "x", "2026-10-05"))
        services.schedule_task(con, tid, "2026-10-06", "15:00")
        assert [s["key"] for s in calendar_sync.stale(con, date(2026, 10, 1))] == [ev[0]["key"]]
        assert [w["key"] for w in calendar_sync.pending(con, date(2026, 10, 1)) if w["key"].startswith("block:")] == [f"block:{tid}:2026-10-06:15:00"]


def test_habit_linked_to_project_and_morning_message_has_blocks(env):
    db, _, services = env
    from pos import briefing, life, tgfmt
    with db.session() as con:
        services.add_project(con, "Aprender inglés", area="estudio")
        life.add_habit(con, "Practicar inglés 30 min", project="Aprender inglés")
        h = life.habit_status(con)[0]
        assert h["project"] == "Aprender inglés" and h["project_id"]
        tid, _ = services.add_quick(con, f"clase de inglés hoy 7pm ~45m #inglés")
        con.execute("UPDATE tasks SET scheduled_date=date('now','localtime') WHERE id=?", (tid,))
        msg = tgfmt.morning(briefing.gather(con))
        assert "Tu día" in msg and "19:00" in msg and "Clase de inglés" in msg


def test_autotasks_create_gift_and_renewal_once(env):
    db, _, services = env
    from pos import autotasks, life
    today = date(2026, 11, 28)
    with db.session() as con:
        life.add_person(con, "Mamá", "mamá", "12-28", circle="familia")
        con.execute("INSERT INTO documents(name, expires_on) VALUES ('Pasaporte', '2026-12-20')")
        assert autotasks.run(con, today) == 2                                                                 # regalo + «preparar» (faltan 22 días: ≤30)
        rows = {r["title"]: r for r in con.execute("SELECT title, due_date, priority, tags FROM tasks WHERE source='auto'")}
        gift = next(r for k, r in rows.items() if "regalo" in k)
        assert gift["due_date"] == "2026-12-21" and "Mamá" in next(iter(k for k in rows if "regalo" in k))   # una semana antes
        prep = next(r for k, r in rows.items() if "Preparar" in k and "Pasaporte" in k)
        assert prep["priority"] == 2 and prep["due_date"] == "2026-12-05" and "vencimiento" in prep["tags"]  # 15 días antes del vencimiento
        assert autotasks.run(con, today) == 0                                                                 # idempotente
        services.drop_task(con, con.execute("SELECT id FROM tasks WHERE title LIKE '%regalo%'").fetchone()[0])
        assert autotasks.run(con, today) == 0                                                                 # descartada: no vuelve


def test_documents_ask_at_30_and_15_days_and_roll_over_every_year(env):
    db, _, services = env
    from pos import autotasks
    with db.session() as con:
        con.execute("INSERT INTO documents(name, expires_on) VALUES ('SOAT del vehículo', '2027-07-19')")
        assert autotasks.run(con, date(2027, 6, 1)) == 0                                                      # lejos: nada
        assert autotasks.run(con, date(2027, 6, 19)) == 1                                                     # 30 días antes: qué preparar
        prep = con.execute("SELECT title, notes FROM tasks WHERE title LIKE 'Preparar%' OR title LIKE '%Preparar%'").fetchone()
        assert "SOAT" in prep["title"] and "cotiza" in prep["notes"].lower()
        assert autotasks.run(con, date(2027, 7, 4)) == 1                                                      # 15 días antes: comprarlo
        renew = con.execute("SELECT id, title, priority, due_date FROM tasks WHERE title LIKE '%Comprar el SOAT%'").fetchone()
        assert renew["priority"] == 1 and renew["due_date"] == "2027-07-16"
        services.done_task(con, renew["id"])
        autotasks.run(con, date(2027, 7, 10))
        assert con.execute("SELECT expires_on FROM documents").fetchone()[0] == "2028-07-19"               # el ciclo sigue el año siguiente
        assert autotasks.run(con, date(2027, 7, 11)) == 0


def test_finance_panel_charts_insights_and_auto_import(env, tmp_path):
    db, _, _ = env
    from pos import finance, money
    csv_text = ("Extracto Bancolombia\nCuenta ****1234\n\nFecha;Descripción;Débito;Crédito;Saldo\n"
                "01/09/2026;SUPERMERCADO EXITO 123;250.000,00;;1.000.000\n05/09/2026;NOMINA EMPRESA;;4.000.000,00;5.000.000\n"
                "09/09/2026;NETFLIX.COM;38.900,00;;4.900.000\n12/09/2026;RAPPI *PEDIDO 889;62.500,00;;4.800.000\n")
    f = tmp_path / "ext.csv"
    f.write_text(csv_text, encoding="latin-1")
    with db.session() as con:
        con.execute("INSERT INTO accounts(name, kind) VALUES ('Bancolombia', 'bank')")
        added, skipped, found = finance.import_auto(con, f, "Bancolombia")
        assert (added, skipped) == (4, 0) and found["columns"]["debit"] == "Débito" and found["delimiter"] == ";" and found["header_row"] == 2
        assert finance.import_auto(con, f, "Bancolombia")[:2] == (0, 4)                          # reimportar no duplica
        s = money.month_summary(con, "2026-09")
        assert s["income"] == 4000000 and s["expense"] == 351400
        tips = finance.insights(con, "2026-09", today=date(2026, 9, 20))
        assert any("Ahorraste" in t["text"] and t["level"] == "ok" for t in tips)
        finance.set_balance(con, "Bancolombia", 5000000, "2026-08-31")
        finance.set_balance(con, "Bancolombia", 4800000, "2026-09-30")
        con.execute("INSERT INTO savings_goals(name, target, deadline, saved) VALUES ('Aruba', 6000000, '2027-01-15', 1500000)")
        g = finance.goals(con)[0]
        assert g["pct"] == 25 and g["per_month"] and g["per_month"] > 0
        series = finance.monthly_series(con, 6, None, "2026-09")
        assert series[-1]["expense"] == 351400 and series[-1]["income"] == 4000000
        svg = finance.bars_svg(series)
        donut, legend = finance.donut_svg([{"name": "Mercado", "value": 250000}, {"name": "Ocio", "value": 101400}])
        line = finance.line_svg(finance.networth_series(con))
        assert svg.startswith("<svg") and "<rect" in svg and donut.count("<circle") == 2 and legend[0]["pct"] == 71 and "<path" in line
        assert finance.heat_grid(finance.daily_spend(con, "2026-09"), "2026-09")["max"] == 250000
        assert finance.top_merchants(con, "2026-09")[0]["name"].startswith("Supermercado")


def test_google_changes_flow_back_to_tasks(env):
    db, _, services = env
    from pos import calendar_sync
    with db.session() as con:
        t1, _ = services.add_quick(con, "informe 2026-10-05 10am ~60m")
        t2, _ = services.add_quick(con, "llamada 2026-10-05 3pm ~30m")
        con.execute("UPDATE tasks SET scheduled_date='2026-10-05' WHERE id IN (?,?)", (t1, t2))
        con.execute("INSERT INTO calendar_sync(key, event_id, title, date) VALUES (?,?,?,?)", (f"block:{t1}:2026-10-05:10:00", "g1", "x", "2026-10-05"))
        con.execute("INSERT INTO calendar_sync(key, event_id, title, date) VALUES (?,?,?,?)", (f"block:{t2}:2026-10-05:15:00", "g2", "y", "2026-10-05"))
        events = [{"id": "g1", "summary": "⏱ Informe", "start": {"dateTime": "2026-10-06T09:00:00-05:00"}, "end": {"dateTime": "2026-10-06T10:30:00-05:00"}}]  # g2 ya no existe
        r = calendar_sync.sync_back(con, events, "2026-10-01", "2026-12-31")
        assert r == {"moved": 1, "removed": 1}
        a, b = services.task_row(con, t1), services.task_row(con, t2)
        assert (a["scheduled_date"], a["start_time"], a["est_minutes"]) == ("2026-10-06", "09:00", 90)   # lo moviste en Google: la tarea te sigue
        assert b["start_time"] is None and b["scheduled_date"] is None                                   # lo borraste en Google: vuelve a «sin hora»
        keys = [x["key"] for x in con.execute("SELECT key FROM calendar_sync")]
        assert keys == [f"block:{t1}:2026-10-06:09:00"]                                                  # la clave se actualiza: no se duplica ni se borra el evento


def test_weekly_review_flags_open_loops(env):
    db, _, services = env
    from pos import review
    with db.session() as con:
        services.add_project(con, "Casa")
        services.add_project(con, "Vacío")
        services.add_task(con, "Pintar sala", project="Casa", due="2020-01-01")
        con.execute("INSERT INTO tasks(title, created_at) VALUES ('Idea vieja', '2020-01-01 00:00:00')")
        r = review.build(con, date(2026, 10, 1))
        assert r["overdue"] and r["old"] and [p["name"] for p in r["no_next"]] == ["Vacío"] and r["score"] < len(r["steps"])
