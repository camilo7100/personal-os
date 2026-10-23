"""Sincronización con Google Calendar de lo que NO puede depender de que el PC esté encendido.
Este módulo decide QUÉ debe estar en el calendario y recuerda qué ya está; los eventos los crea Claude con el conector de
Google Calendar (skill `calendar-sync`), porque los jobs de Windows no tienen credenciales de Google. Google avisa al celular solo."""
import json
from datetime import date, datetime, timedelta

import typer

from . import dates, db
from .util import JSON

app = typer.Typer(help="Recordatorios en Google Calendar", no_args_is_help=True)
HORIZON_DAYS = 120
TZ = "America/Bogota"


def _rem(days_before: int) -> int:
    """Minutos antes de un evento de día completo para que suene a las 9:00 de ese día previo."""
    return days_before * 1440 - 540


def wanted(con, today: date | None = None) -> list[dict]:
    today = today or date.today()
    limit = today + timedelta(days=HORIZON_DAYS)
    ok = lambda d: today <= date.fromisoformat(d) <= limit
    out: list[dict] = []
    for r in con.execute("SELECT id, name, expires_on FROM documents WHERE expires_on IS NOT NULL"):
        if ok(r["expires_on"]):
            out.append(dict(key=f"doc:{r['id']}:{r['expires_on']}", title=f"📄 Vence: {r['name']}", date=r["expires_on"], all_day=True,
                            reminders=[_rem(30), _rem(7), _rem(1)], description="Documento con vencimiento (PERSONAL_OS)."))
    for r in con.execute("SELECT id, name, amount, next_date FROM subscriptions WHERE active=1 AND next_date IS NOT NULL"):
        if ok(r["next_date"]):
            out.append(dict(key=f"sub:{r['id']}:{r['next_date']}", title=f"🔁 Se cobra {r['name']} ({r['amount']:,.0f})", date=r["next_date"], all_day=True,
                            reminders=[_rem(1)], description="Suscripción (PERSONAL_OS)."))
    close_rem = [_rem(n) for n in sorted(set(dates.CLOSE_LEADS) - {0}, reverse=True)] + [_rem(1), _rem(0)]
    for r in con.execute("SELECT id, name, birthday, circle FROM people WHERE birthday IS NOT NULL"):
        try:
            nxt = dates.next_occurrence(r["birthday"], today)
        except ValueError:
            continue
        near = r["circle"] in dates.CLOSE
        out.append(dict(key=f"bday:{r['id']}", title=f"🎂 Cumpleaños de {r['name']}", date=nxt.isoformat(), all_day=True,
                        reminders=close_rem if near else [_rem(1)], recurrence="RRULE:FREQ=YEARLY", description="Cumpleaños (PERSONAL_OS)."))
    for r in con.execute("SELECT s.id, s.title, s.rule, p.name AS who FROM special_dates s LEFT JOIN people p ON p.id=s.person_id"):
        out.append(dict(key=f"special:{r['id']}", title=f"💝 {r['title']}", date=dates.next_occurrence(r["rule"], today).isoformat(), all_day=True,
                        reminders=close_rem, recurrence=dates.rrule(r["rule"]),
                        description=f"Fecha especial{' de ' + r['who'] if r['who'] else ''} (PERSONAL_OS). Avisa 30 y 15 días antes."))
    for r in con.execute("SELECT id, title, scheduled_date, start_time, est_minutes FROM tasks WHERE status IN ('todo','doing','blocked') AND parent_id IS NULL AND start_time IS NOT NULL AND scheduled_date IS NOT NULL"):
        if ok(r["scheduled_date"]):
            out.append(dict(key=f"block:{r['id']}:{r['scheduled_date']}:{r['start_time']}", title=f"⏱ {r['title']}", date=r["scheduled_date"], all_day=False, start_time=r["start_time"],
                            duration_min=r["est_minutes"] or 30, reminders=[10], description="Bloque de tiempo (PERSONAL_OS). Se mueve solo si lo cambias en el portal."))
    for r in con.execute("SELECT a.id, a.next_step, a.next_step_date, a.role, c.name AS company FROM applications a JOIN companies c ON c.id=a.company_id "
                         "WHERE a.status IN ('saved','applied','interview','offer') AND a.next_step_date IS NOT NULL"):
        if ok(r["next_step_date"]):
            out.append(dict(key=f"app:{r['id']}:{r['next_step_date']}", title=f"💼 {r['company']}: {r['next_step'] or 'siguiente paso'}", date=r["next_step_date"],
                            all_day=False, start_time="09:00", duration_min=30, reminders=[60, 1440], description=r["role"]))
    for r in con.execute("SELECT id, title, due_date, tags FROM tasks WHERE status IN ('todo','doing','blocked') AND due_date IS NOT NULL AND (priority=1 OR tags LIKE '%pago%' OR tags LIKE '%finanzas%' OR tags LIKE '%vencimiento%')"):
        if ok(r["due_date"]):
            pay = "pago" in (r["tags"] or "") or "finanzas" in (r["tags"] or "")
            out.append(dict(key=f"task:{r['id']}:{r['due_date']}", title=f"{'💳' if 'pago' in (r['tags'] or '') else '📊' if pay else '❗'} {r['title']}", date=r["due_date"], all_day=True,
                            reminders=[_rem(1), _rem(0)] if pay else [_rem(1)], description="Recordatorio recurrente (PERSONAL_OS)." if pay else "Tarea urgente (PERSONAL_OS)."))
    return out


def pending(con, today: date | None = None) -> list[dict]:
    synced = {r["key"] for r in con.execute("SELECT key FROM calendar_sync")}
    return [w for w in wanted(con, today) if w["key"] not in synced]


def stale(con, today: date | None = None) -> list[dict]:
    """Eventos ya creados que dejaron de hacer falta (tarea hecha, fecha cambiada, documento renovado)."""
    today = today or date.today()
    keep = {w["key"] for w in wanted(con, today)}
    return [dict(r) for r in con.execute("SELECT key, event_id, title, date FROM calendar_sync") if r["key"] not in keep and (r["date"] or "") >= (today - timedelta(days=1)).isoformat()]


@app.command("pending")
def pending_cmd(as_json: bool = JSON) -> None:
    """Qué falta por crear en Google Calendar."""
    with db.session() as con:
        items = pending(con)
    if as_json:
        typer.echo(json.dumps(items, ensure_ascii=False, indent=2))
    else:
        for i in items:
            typer.echo(f"{i['date']}  {i['title']}  [{i['key']}]")
        typer.echo(f"\n{len(items)} pendientes. Pídele a Claude: «sincroniza mi calendario».")


@app.command("mark")
def mark_cmd(key: str, event_id: str, title: str = "", date_: str = typer.Option("", "--date")) -> None:
    """Registra que el evento ya existe en Google Calendar."""
    with db.session() as con:
        con.execute("INSERT OR REPLACE INTO calendar_sync(key, event_id, title, date) VALUES (?,?,?,?)", (key, event_id, title, date_))
        db.audit(con, "calendar.mark", "WRITE", key, detail=event_id)
    typer.echo("ok")


@app.command("stale")
def stale_cmd(as_json: bool = JSON) -> None:
    with db.session() as con:
        items = stale(con)
    typer.echo(json.dumps(items, ensure_ascii=False, indent=2) if as_json else "\n".join(f"{i['key']} {i['event_id']} {i['title']}" for i in items) or "(nada)")


@app.command("forget")
def forget_cmd(key: str) -> None:
    """Olvida un evento (después de borrarlo de Google Calendar)."""
    with db.session() as con:
        con.execute("DELETE FROM calendar_sync WHERE key=?", (key,))
        db.audit(con, "calendar.forget", "WRITE", key)
    typer.echo("ok")


# ---------- Google → PERSONAL_OS: si mueves o borras un bloque en Google Calendar, la tarea te sigue ----------
def sync_back(con, events: list[dict], start: str, end: str) -> dict:
    """`events`: lo que devuelve el conector de Google Calendar para el rango [start, end]. Solo toca bloques de tiempo que PERSONAL_OS creó (clave `block:`)."""
    from . import agenda, services
    by_id = {e.get("id"): e for e in events}
    moved = removed = 0
    for r in con.execute("SELECT key, event_id, date FROM calendar_sync WHERE key LIKE 'block:%'").fetchall():
        if not (start <= (r["date"] or "") <= end):
            continue
        _, tid, old_day, old_time = r["key"].split(":", 3)
        ev = by_id.get(r["event_id"])
        if ev is None or ev.get("status") == "cancelled":   # lo borraste en Google: la tarea vuelve a «sin hora»
            services.unschedule_task(con, int(tid), "google")
            con.execute("DELETE FROM calendar_sync WHERE key=?", (r["key"],))
            removed += 1
            continue
        a, all_day = agenda._local(ev.get("start"))
        b, _ = agenda._local(ev.get("end"))
        if not a or all_day:
            continue
        day, hhmm = a[:10], a[11:16]
        if (day, hhmm) != (old_day, old_time):
            minutes = None
            if b and b[:10] == day:
                (h1, m1), (h2, m2) = map(int, hhmm.split(":")), map(int, b[11:16].split(":"))
                minutes = max(5, h2 * 60 + m2 - h1 * 60 - m1)
            services.schedule_task(con, int(tid), day, hhmm, minutes, "google")
            con.execute("DELETE FROM calendar_sync WHERE key=?", (r["key"],))
            con.execute("INSERT OR REPLACE INTO calendar_sync(key, event_id, title, date) VALUES (?,?,?,?)", (f"block:{tid}:{day}:{hhmm}", r["event_id"], ev.get("summary") or "", day))
            moved += 1
    db.audit(con, "calendar.sync_back", "WRITE", f"{start}..{end}", detail=f"{moved} movidos, {removed} quitados", actor="job")
    return {"moved": moved, "removed": removed}


@app.command("pull")
def pull_cmd(file: str = typer.Argument(..., help="JSON con los eventos de Google (list_events)"), start: str = typer.Option(None), end: str = typer.Option(None)) -> None:
    """Aplica a las tareas los cambios hechos en Google Calendar (bloques movidos o borrados) y refresca la copia local."""
    from . import agenda
    data = json.loads(open(file, encoding="utf-8").read())
    events = data.get("events", data) if isinstance(data, dict) else data
    s = start or date.today().isoformat()
    e = end or (date.today() + timedelta(days=HORIZON_DAYS)).isoformat()
    with db.session() as con:
        r = sync_back(con, events, s, e)
        c = agenda.cache_events(con, events, "primary", s, e)
    typer.echo(f"Google → PERSONAL_OS: {r['moved']} bloques movidos, {r['removed']} quitados · {c['events']} eventos en la copia local")
