"""Planificador del día: capacidad realista, huecos libres y propuesta de bloques de tiempo.
Reglas tomadas de lo que recomiendan los líderes (resumen en DECISIONS.md D-046):
  · Sunsama/Akiflow: sumar lo estimado y compararlo con un límite diario; avisar antes de que el plan no quepa.
  · Cal Newport: ~4 h de trabajo profundo al día es el techo para casi todos → capacidad por defecto 5 h de tareas (4 h profundas + ligeras).
  · Regla 1-3-5 / Ivy Lee: como máximo 1 tarea grande, 3 medianas y 5 pequeñas (9 en total; las 6 más importantes primero).
  · Motion/Reclaim: poner cada tarea en un hueco libre del calendario, respetando horario laboral, almuerzo y colchón entre bloques.
Todo es determinista y editable en config\\planner.json."""
import json
from datetime import date, datetime, timedelta

import typer

from . import db, services
from .util import JSON

app = typer.Typer(help="Planificador del día (capacidad y bloques de tiempo)", no_args_is_help=True)
CONFIG = db.ROOT / "config" / "planner.json"
DEFAULTS = {
    "work_start": "08:00", "work_end": "19:00", "lunch": ["12:30", "13:30"], "buffer_min": 10,
    "capacity_minutes": 300,   # 5 h de tareas al día
    "max_items": 9,            # 1 grande + 3 medianas + 5 pequeñas
    "max_big": 1, "max_medium": 3, "max_small": 5,
    "big_min": 90, "small_max": 25,   # grande ≥ 90 min · pequeña ≤ 25 min · mediana el resto
    "default_minutes": 30, "round_to": 15,
    "weekend_capacity_minutes": 120,
}


def cfg() -> dict:
    try:
        return {**DEFAULTS, **json.loads(CONFIG.read_text(encoding="utf-8"))}
    except Exception:
        return dict(DEFAULTS)


def _m(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def _hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def size_of(minutes: int | None, c: dict | None = None) -> str:
    c = c or cfg()
    m = minutes or c["default_minutes"]
    return "grande" if m >= c["big_min"] else "pequeña" if m <= c["small_max"] else "mediana"


def day_tasks(con, day: date) -> list[dict]:
    """Lo que hay que hacer ese día: tareas programadas o con vencimiento ese día (y, para hoy, lo vencido) + ítems de planes."""
    iso = day.isoformat()
    today = date.today().isoformat()
    over = f"OR (due_date < '{iso}' AND scheduled_date IS NULL)" if iso == today else ""
    rows = [dict(r, src="tarea") for r in con.execute(
        f"SELECT id, title, priority, due_date, scheduled_date, start_time, est_minutes FROM tasks WHERE status IN ('todo','doing','blocked') AND parent_id IS NULL AND someday=0 "
        f"AND (scheduled_date=? OR (scheduled_date IS NULL AND due_date=?) {over}) ORDER BY priority, COALESCE(start_time,'99:99'), id", (iso, iso))]
    if iso == today:
        from . import plans
        rows += [dict(id=i["id"], title=f"{i['plan'][:24]}: {i['title']}", priority=3, due_date=i["scheduled_date"], scheduled_date=i["scheduled_date"], start_time=None,
                      est_minutes=i["est_minutes"], src="plan") for i in plans.plan_today(con, iso)[:12]]
    else:
        rows += [dict(id=r["id"], title=r["title"], priority=3, due_date=iso, scheduled_date=iso, start_time=None, est_minutes=r["est_minutes"], src="plan")
                 for r in con.execute("SELECT i.id, i.title, i.est_minutes FROM plan_items i JOIN plans p ON p.id=i.plan_id WHERE p.status='active' AND i.status='todo' AND i.scheduled_date=?", (iso,))]
    return rows


def load(con, day: date | None = None) -> dict:
    """Carga del día frente a la capacidad realista, con consejo concreto."""
    c = cfg()
    day = day or date.today()
    rows = day_tasks(con, day)
    cap = c["weekend_capacity_minutes"] if day.weekday() >= 5 else c["capacity_minutes"]
    mins = sum((r["est_minutes"] or c["default_minutes"]) for r in rows)
    sizes = {"grande": 0, "mediana": 0, "pequeña": 0}
    for r in rows:
        sizes[size_of(r["est_minutes"], c)] += 1
    n = len(rows)
    pct = round(100 * mins / cap) if cap else 0
    state = "over" if (pct > 100 or n > c["max_items"]) else "warn" if (pct >= 80 or n >= c["max_items"] - 1) else "ok"
    if state == "over":
        extra = max(0, mins - cap)
        advice = f"Demasiado para un día: {n} tareas, {mins // 60} h {mins % 60} min (tu capacidad: {cap // 60} h). Mueve lo menos importante (≈{extra} min) a otro día."
    elif state == "warn":
        advice = "Día casi lleno: no agregues más; deja colchón para imprevistos."
    elif n == 0:
        advice = "Día libre: buen momento para adelantar algo importante."
    else:
        free = cap - mins
        advice = f"Te caben ≈{free // 60} h {free % 60} min más. Recomendado: 1 grande, 3 medianas y 5 pequeñas como máximo."
    mit = sorted([r for r in rows if r["src"] == "tarea"], key=lambda r: (r["priority"], r["due_date"] or "9999"))[:3]
    return {"day": day.isoformat(), "count": n, "minutes": mins, "capacity": cap, "pct": pct, "state": state, "sizes": sizes, "advice": advice, "mit": mit,
            "max_items": c["max_items"]}


def week_load(con, start: date) -> list[dict]:
    return [load(con, start + timedelta(days=i)) for i in range(7)]


def busy(con, day: date) -> list[tuple[int, int, str]]:
    """Intervalos ocupados (minutos del día): eventos de Google con hora, bloques de tareas y almuerzo."""
    c = cfg()
    iso = day.isoformat()
    out = [(_m(c["lunch"][0]), _m(c["lunch"][1]), "Almuerzo")]
    for r in con.execute("SELECT title, start, end FROM gcal_events WHERE all_day=0 AND substr(start,1,10)=?", (iso,)):
        a = _m(r["start"][11:16])
        b = _m(r["end"][11:16]) if r["end"] and r["end"][:10] == iso else a + 60
        out.append((a, max(b, a + 15), r["title"]))
    for r in con.execute("SELECT title, start_time, est_minutes FROM tasks WHERE status IN ('todo','doing','blocked') AND scheduled_date=? AND start_time IS NOT NULL", (iso,)):
        a = _m(r["start_time"])
        out.append((a, a + (r["est_minutes"] or c["default_minutes"]), r["title"]))
    return sorted(out)


def free_slots(con, day: date, now: datetime | None = None) -> list[tuple[int, int]]:
    c = cfg()
    lo, hi = _m(c["work_start"]), _m(c["work_end"])
    if now and day == now.date():
        step = c["round_to"]
        lo = max(lo, ((now.hour * 60 + now.minute + step - 1) // step) * step)
    slots, cur = [], lo
    for a, b, _ in busy(con, day):
        b += c["buffer_min"]
        if a > cur:
            slots.append((cur, min(a, hi)))
        cur = max(cur, b)
    if cur < hi:
        slots.append((cur, hi))
    return [(a, b) for a, b in slots if b - a >= 15]


def suggest(con, day: date | None = None, now: datetime | None = None) -> dict:
    """Propone bloques de tiempo para las tareas del día sin hora, de lo más importante a lo menos, sin pasarse de la capacidad ni del 1-3-5."""
    c = cfg()
    day = day or date.today()
    now = now or datetime.now()
    pool = [r for r in day_tasks(con, day) if r["src"] == "tarea" and not r["start_time"]]
    pool.sort(key=lambda r: (0 if (r["due_date"] or "9999") <= day.isoformat() else 1, r["priority"], r["due_date"] or "9999", -(r["est_minutes"] or 0)))
    slots = free_slots(con, day, now)
    used = sum((r["est_minutes"] or c["default_minutes"]) for r in day_tasks(con, day) if r["start_time"])
    cap = c["weekend_capacity_minutes"] if day.weekday() >= 5 else c["capacity_minutes"]
    quota = {"grande": c["max_big"], "mediana": c["max_medium"], "pequeña": c["max_small"]}
    plan, left_out = [], []
    for r in pool:
        need = r["est_minutes"] or c["default_minutes"]
        sz = size_of(r["est_minutes"], c)
        if used + need > cap or quota[sz] <= 0:
            left_out.append(r)
            continue
        for i, (a, b) in enumerate(slots):
            if b - a >= need:
                plan.append({"id": r["id"], "title": r["title"], "start": _hm(a), "end": _hm(a + need), "minutes": need, "size": sz})
                slots[i] = (a + need + c["buffer_min"], b)
                used += need
                quota[sz] -= 1
                break
        else:
            left_out.append(r)
    plan.sort(key=lambda p: p["start"])
    return {"day": day.isoformat(), "plan": plan, "left_out": [{"id": r["id"], "title": r["title"], "minutes": r["est_minutes"] or c["default_minutes"]} for r in left_out]}


def apply(con, plan: list[dict], day: date, actor: str | None = None) -> int:
    n = 0
    for p in plan:
        n += bool(services.schedule_task(con, p["id"], day.isoformat(), p["start"], p["minutes"], actor))
    return n


def rebalance(con, start: date) -> list[dict]:
    """Para los días sobrecargados de la semana: qué mover (lo no urgente) y a qué día más libre antes de su vencimiento."""
    loads = {l["day"]: l for l in week_load(con, start)}
    moves = []
    for iso, l in loads.items():
        if l["state"] != "over":
            continue
        d = date.fromisoformat(iso)
        movable = sorted([r for r in day_tasks(con, d) if r["src"] == "tarea" and r["priority"] >= 3 and not r["start_time"]], key=lambda r: (-r["priority"], r["due_date"] or "9999"), reverse=False)
        excess = l["minutes"] - l["capacity"]
        for r in movable:
            if excess <= 0:
                break
            targets = [x for k, x in loads.items() if k > iso and x["state"] == "ok" and (not r["due_date"] or k <= r["due_date"])]
            if targets:
                t = min(targets, key=lambda x: x["pct"])
                moves.append({"id": r["id"], "title": r["title"], "from": iso, "to": t["day"]})
                excess -= r["est_minutes"] or cfg()["default_minutes"]
    return moves


@app.command("today")
def today_cmd(as_json: bool = JSON) -> None:
    """Carga de hoy y bloques sugeridos."""
    with db.session() as con:
        l, s = load(con), suggest(con)
    if as_json:
        typer.echo(json.dumps({"load": l, "suggest": s}, ensure_ascii=False, indent=2, default=str))
        return
    typer.echo(f"Hoy: {l['count']} tareas · {l['minutes']} min de {l['capacity']} ({l['pct']}%) · {l['advice']}")
    for p in s["plan"]:
        typer.echo(f"  {p['start']}–{p['end']}  {p['title']} ({p['size']})")
    for r in s["left_out"]:
        typer.echo(f"  (no cabe) {r['title']}")


@app.command("apply")
def apply_cmd() -> None:
    """Aplica los bloques sugeridos de hoy."""
    with db.session() as con:
        n = apply(con, suggest(con)["plan"], date.today(), "cli")
    typer.echo(f"{n} bloques programados")
