"""Agenda unificada: todo lo que tiene fecha, en un solo lugar (tareas y bloques de tiempo, planes, eventos de Google Calendar, familia y pareja,
cumpleaños, festivos de Colombia, vencimientos, cobros y pasos de empleo). Es lo que dibujan las vistas Mes, Semana, Día, Agenda y Línea de tiempo."""
import json
from datetime import date, datetime, timedelta, timezone

import typer

from . import dates, db
from .util import JSON

app = typer.Typer(help="Agenda unificada y copia local de Google Calendar", no_args_is_help=True)
BOGOTA = timezone(timedelta(hours=-5))
KINDS = {  # clave → (etiqueta, clase de color en el portal)
    "bloque": ("Bloque de tiempo", "k-bloque"), "tarea": ("Tarea", "k-tarea"), "vence": ("Vence", "k-vence"), "pago": ("Pago", "k-pago"),
    "evento": ("Google Calendar", "k-evento"), "familia": ("Familia y pareja", "k-familia"), "cumple": ("Cumpleaños", "k-cumple"),
    "festivo": ("Festivo", "k-festivo"), "plan": ("Plan de estudio", "k-plan"), "documento": ("Documento", "k-documento"),
    "cobro": ("Suscripción", "k-cobro"), "empleo": ("Empleo", "k-empleo"),
}


def _t(hhmm: str | None, minutes: int | None, default: int = 30) -> str | None:
    if not hhmm:
        return None
    h, m = map(int, hhmm.split(":"))
    end = h * 60 + m + (minutes or default)
    return f"{min(end, 1439) // 60:02d}:{min(end, 1439) % 60:02d}"


def items(con, start: date, end: date, kinds: set[str] | None = None, collapse_plans: bool = False) -> list[dict]:
    """Todo lo fechado entre `start` y `end` (inclusive), ordenado por día y hora. `collapse_plans`: una sola línea por día para los ítems de planes de estudio."""
    s, e = start.isoformat(), end.isoformat()
    out: list[dict] = []

    def add(day, kind, title, time=None, end_time=None, link=None, id=None, done=False, minutes=None, who=None):
        if kinds and kind not in kinds:
            return
        out.append(dict(date=day, kind=kind, cls=KINDS[kind][1], title=title, time=time, end_time=end_time, link=link, id=id, done=done, minutes=minutes, who=who))

    # tareas: bloque de tiempo (con hora), tarea del día y marcador de vencimiento
    for r in con.execute("SELECT id, title, due_date, scheduled_date, start_time, est_minutes, tags, status, parent_id FROM tasks WHERE status IN ('todo','doing','blocked','done') "
                         "AND parent_id IS NULL AND someday=0 AND (COALESCE(scheduled_date, due_date) BETWEEN ? AND ? OR due_date BETWEEN ? AND ?)", (s, e, s, e)):
        done = r["status"] == "done"
        pay = "pago" in (r["tags"] or "")
        work_day = r["scheduled_date"] or r["due_date"]
        if work_day and s <= work_day <= e:
            if r["start_time"] and r["scheduled_date"]:
                add(work_day, "bloque", r["title"], r["start_time"], _t(r["start_time"], r["est_minutes"]), f"/tasks/{r['id']}", r["id"], done, r["est_minutes"])
            else:
                add(work_day, "pago" if pay else "tarea", r["title"], None, None, f"/tasks/{r['id']}", r["id"], done, r["est_minutes"])
        if r["scheduled_date"] and r["due_date"] and r["due_date"] != r["scheduled_date"] and s <= r["due_date"] <= e:
            add(r["due_date"], "vence", f"Vence: {r['title']}", None, None, f"/tasks/{r['id']}", r["id"], done)

    # planes de estudio
    for r in con.execute("SELECT i.id, i.title, i.est_minutes, i.scheduled_date, i.status, p.id AS plan_id, p.title AS plan FROM plan_items i JOIN plans p ON p.id=i.plan_id "
                         "WHERE p.status='active' AND i.scheduled_date BETWEEN ? AND ?", (s, e)):
        add(r["scheduled_date"], "plan", f"{r['plan'][:28]}: {r['title']}", None, None, f"/plans/{r['plan_id']}", None, r["status"] == "done", r["est_minutes"])

    # eventos de Google Calendar (copia local)
    for r in con.execute("SELECT uid, calendar, title, start, end, all_day FROM gcal_events WHERE substr(start,1,10) <= ? AND substr(COALESCE(end, start),1,10) >= ?", (e, s)):
        d0, d1 = date.fromisoformat(r["start"][:10]), date.fromisoformat((r["end"] or r["start"])[:10])
        if r["all_day"]:
            d1 -= timedelta(days=1)  # el fin de un evento de día completo es exclusivo
        d = max(d0, start)
        while d <= min(max(d1, d0), end):
            add(d.isoformat(), "evento", r["title"], None if r["all_day"] else r["start"][11:16], None if r["all_day"] else (r["end"] or "")[11:16] or None, None, r["uid"])
            d += timedelta(days=1)

    # familia, pareja y cumpleaños
    for p in con.execute("SELECT id, name, relation, birthday, circle FROM people WHERE birthday IS NOT NULL"):
        for y in range(start.year, end.year + 1):
            try:
                d = dates.occurrence(p["birthday"], y)
            except ValueError:
                continue
            if start <= d <= end:
                near = p["circle"] in dates.CLOSE
                add(d.isoformat(), "familia" if near else "cumple", f"Cumpleaños de {p['name']}", None, None, "/life", None, who=p["relation"])
    for sp in con.execute("SELECT id, title, rule FROM special_dates"):
        for y in range(start.year, end.year + 1):
            d = dates.occurrence(sp["rule"], y)
            if start <= d <= end:
                add(d.isoformat(), "familia", sp["title"], None, None, "/life")

    # festivos de Colombia
    for y in range(start.year, end.year + 1):
        for d, name in dates.holidays(y).items():
            if start <= d <= end:
                add(d.isoformat(), "festivo", name)

    # vencimientos, cobros y empleo
    for r in con.execute("SELECT id, name, expires_on FROM documents WHERE expires_on BETWEEN ? AND ?", (s, e)):
        add(r["expires_on"], "documento", f"Vence: {r['name']}", None, None, "/life")
    for r in con.execute("SELECT id, name, amount, next_date FROM subscriptions WHERE active=1 AND next_date BETWEEN ? AND ?", (s, e)):
        add(r["next_date"], "cobro", f"Se cobra {r['name']}", None, None, "/life")
    for r in con.execute("SELECT a.id, a.role, a.next_step, a.next_step_date, c.name AS company FROM applications a JOIN companies c ON c.id=a.company_id "
                         "WHERE a.status IN ('saved','applied','interview','offer') AND a.next_step_date BETWEEN ? AND ?", (s, e)):
        add(r["next_step_date"], "empleo", f"{r['company']}: {r['next_step'] or 'siguiente paso'}", None, None, "/career")
    if collapse_plans:
        groups: dict[str, list[dict]] = {}
        rest = []
        for r in out:
            if r["kind"] == "plan":
                groups.setdefault(r["date"], []).append(r)
            else:
                rest.append(r)
        for day, g in groups.items():
            open_ = [r for r in g if not r["done"]]
            mins = sum(r["minutes"] or 0 for r in open_)
            title = f"📚 Estudio: {len(open_)} ítem(s) · {mins} min" if open_ else f"📚 Estudio: {len(g)} ítem(s) hechos"
            rest.append(dict(date=day, kind="plan", cls=KINDS["plan"][1], title=title, time=None, end_time=None, link="/plans", id=None, done=not open_, minutes=mins, who=None))
        out = rest
    return sorted(out, key=lambda x: (x["date"], x["time"] or "99:99", x["kind"], x["title"]))


def by_day(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["date"], []).append(r)
    return out


# ---------- copia local de Google Calendar ----------
def _local(v: dict | str | None) -> tuple[str | None, bool]:
    """Evento de Google (dateTime con zona, o date) → ('YYYY-MM-DD[THH:MM]' en hora de Bogotá, todo_el_día)."""
    if isinstance(v, dict):
        if v.get("dateTime"):
            return datetime.fromisoformat(v["dateTime"].replace("Z", "+00:00")).astimezone(BOGOTA).strftime("%Y-%m-%dT%H:%M"), False
        return (v.get("date") or "")[:10] or None, True
    if v:
        return (str(v)[:10], True) if len(str(v)) <= 10 else (datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(BOGOTA).strftime("%Y-%m-%dT%H:%M"), False)
    return None, True


def cache_events(con, events: list[dict], calendar: str, start: str, end: str) -> dict:
    """Reemplaza la copia del calendario `calendar` entre `start` y `end` por los eventos recibidos (formato de la API de Google)."""
    seen = 0
    con.execute("DELETE FROM gcal_events WHERE calendar=? AND substr(start,1,10) BETWEEN ? AND ?", (calendar, start, end))
    for ev in events:
        if ev.get("status") == "cancelled":
            continue
        # lo que PERSONAL_OS ya creó (o ya conoce: cumpleaños) no se vuelve a mostrar desde Google: saldría duplicado
        if "PERSONAL_OS" in (ev.get("description") or "") or (ev.get("summary") or "").lower().startswith("cumpleaños"):
            continue
        a, all_day = _local(ev.get("start"))
        b, _ = _local(ev.get("end"))
        if not a:
            continue
        con.execute("INSERT OR REPLACE INTO gcal_events(uid, calendar, title, start, end, all_day, location) VALUES (?,?,?,?,?,?,?)",
                    (f"{calendar}:{ev.get('id')}:{a}", calendar, (ev.get("summary") or "(sin título)")[:200], a, b, 1 if all_day else 0, ev.get("location")))
        seen += 1
    db.audit(con, "gcal.cache", "WRITE", calendar, detail=f"{seen} eventos {start}..{end}", actor="job")
    return {"calendar": calendar, "events": seen}


@app.command("cache")
def cache_cmd(file: str = typer.Argument(..., help="JSON con los eventos del conector de Google Calendar"), calendar: str = typer.Option("primary"),
              start: str = typer.Option(None, help="YYYY-MM-DD (por defecto hoy)"), end: str = typer.Option(None, help="YYYY-MM-DD (por defecto +90 días)")) -> None:
    """Guarda una copia local de los eventos de Google Calendar (la corre Claude con el conector) para verlos en el portal y evitar choques al planificar."""
    events = json.loads(open(file, encoding="utf-8").read())
    if isinstance(events, dict):
        events = events.get("events", [])
    s = start or date.today().isoformat()
    e = end or (date.today() + timedelta(days=90)).isoformat()
    with db.session() as con:
        r = cache_events(con, events, calendar, s, e)
    typer.echo(f"{r['events']} eventos guardados de «{calendar}» ({s} → {e})")


@app.command("list")
def list_cmd(days: int = 14, as_json: bool = JSON) -> None:
    """Qué viene en los próximos días (todo junto)."""
    with db.session() as con:
        rows = items(con, date.today(), date.today() + timedelta(days=days))
    if as_json:
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            typer.echo(f"{r['date']} {r['time'] or '     '} [{r['kind']}] {r['title']}")
