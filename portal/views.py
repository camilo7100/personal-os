"""Contextos de las vistas del gestor de tareas y del calendario (funciones puras sobre la base; las rutas viven en app.py)."""
from datetime import date, timedelta

from pos import agenda, dates, planner, plans

DAY_NAMES = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MONTH_NAMES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
HOUR_FROM, HOUR_TO = 6, 22  # rango visible de la cuadrícula de horas
OPEN = "('todo','doing','blocked')"


def nice(d: date) -> str:
    return f"{DAY_NAMES[d.weekday()]} {d.day} de {MONTH_NAMES[d.month]}"


def _mins(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def month_ctx(con, cur: date, kinds: set[str] | None) -> dict:
    first = cur.replace(day=1)
    start = first - timedelta(days=first.weekday())
    end = start + timedelta(days=41)
    by = agenda.by_day(agenda.items(con, start, end, kinds, collapse_plans=True))
    today = date.today()
    weeks = []
    for w in range(6):
        row = []
        for i in range(7):
            d = start + timedelta(days=w * 7 + i)
            its = by.get(d.isoformat(), [])
            load = planner.load(con, d) if its and d >= today - timedelta(days=0) else None
            row.append({"date": d.isoformat(), "num": d.day, "in_month": d.month == first.month, "is_today": d == today, "weekend": d.weekday() >= 5,
                        "items": its[:3], "more": max(0, len(its) - 3), "total": len(its), "state": load["state"] if load and load["count"] else ""})
        weeks.append(row)
    prev, nxt = (first - timedelta(days=1)).replace(day=1), (first + timedelta(days=32)).replace(day=1)
    return {"weeks": weeks, "title": f"{MONTH_NAMES[first.month].capitalize()} {first.year}", "prev": prev.isoformat(), "next": nxt.isoformat(), "dow": DAY_NAMES}


def _lanes(blocks: list[dict]) -> None:
    """Reparte bloques que se solapan en carriles para que no se tapen."""
    blocks.sort(key=lambda b: (b["a"], b["b"]))
    lanes: list[int] = []
    for b in blocks:
        for i, end in enumerate(lanes):
            if end <= b["a"]:
                lanes[i], b["lane"] = b["b"], i
                break
        else:
            lanes.append(b["b"])
            b["lane"] = len(lanes) - 1
    n = max(1, len(lanes))
    for b in blocks:
        b["width"], b["left"] = round(100 / n, 2), round(100 * b["lane"] / n, 2)


def week_ctx(con, cur: date, ndays: int, kinds: set[str] | None) -> dict:
    start = cur - timedelta(days=cur.weekday()) if ndays == 7 else cur
    end = start + timedelta(days=ndays - 1)
    by = agenda.by_day(agenda.items(con, start, end, kinds, collapse_plans=True))
    lo, hi = HOUR_FROM * 60, HOUR_TO * 60
    today = date.today()
    cols = []
    for i in range(ndays):
        d = start + timedelta(days=i)
        its = by.get(d.isoformat(), [])
        blocks = []
        for it in its:
            if not it["time"]:
                continue
            a = max(_mins(it["time"]), lo)
            b = min(_mins(it["end_time"]) if it["end_time"] else a + 45, hi)
            b = max(b, a + 20)
            blocks.append({**it, "a": a, "b": b, "top": round(100 * (a - lo) / (hi - lo), 3), "height": round(100 * (b - a) / (hi - lo), 3)})
        _lanes(blocks)
        load = planner.load(con, d)
        cols.append({"date": d.isoformat(), "label": f"{DAY_NAMES[d.weekday()]} {d.day}", "is_today": d == today, "allday": [x for x in its if not x["time"]][:4], "allday_more": max(0, len([x for x in its if not x["time"]]) - 4), "blocks": blocks,
                     "load": load, "busy": [(round(100 * (a - lo) / (hi - lo), 2), round(100 * (b - a) / (hi - lo), 2)) for a, b, t in planner.busy(con, d) if t == "Almuerzo"]})
    prev, nxt = start - timedelta(days=ndays), start + timedelta(days=ndays)
    title = nice(start) if ndays == 1 else f"{start.day} {MONTH_NAMES[start.month][:3]} – {end.day} {MONTH_NAMES[end.month][:3]} {end.year}"
    hours = [{"h": h, "top": round(100 * (h * 60 - lo) / (hi - lo), 3)} for h in range(HOUR_FROM, HOUR_TO)]
    now = None
    if start <= today <= end:
        import datetime as _dt
        n = _dt.datetime.now()
        if lo <= n.hour * 60 + n.minute <= hi:
            now = round(100 * (n.hour * 60 + n.minute - lo) / (hi - lo), 3)
    return {"cols": cols, "hours": hours, "title": title, "prev": prev.isoformat(), "next": nxt.isoformat(), "now": now, "range_min": hi - lo, "from_min": lo}


def backlog(con, limit: int = 30) -> list[dict]:
    """Tareas sin hora, listas para arrastrar al calendario (lo más urgente primero)."""
    return [dict(r) for r in con.execute(
        f"SELECT id, title, priority, due_date, est_minutes FROM tasks WHERE status IN {OPEN} AND parent_id IS NULL AND someday=0 AND start_time IS NULL "
        f"ORDER BY CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, due_date, priority LIMIT ?", (limit,))]


def agenda_ctx(con, cur: date, span: int, kinds: set[str] | None) -> dict:
    end = cur + timedelta(days=span - 1)
    rows = agenda.items(con, cur, end, kinds)
    by = agenda.by_day(rows)
    days = []
    for i in range(span):
        d = cur + timedelta(days=i)
        its = by.get(d.isoformat())
        if its:
            days.append({"date": d.isoformat(), "label": nice(d), "rel": "hoy" if d == date.today() else "mañana" if d == date.today() + timedelta(days=1) else "", "items": its,
                         "load": planner.load(con, d) if any(x["kind"] in ("tarea", "bloque", "pago", "plan") for x in its) else None})
    return {"days": days, "prev": (cur - timedelta(days=span)).isoformat(), "next": (cur + timedelta(days=span)).isoformat(), "title": f"{nice(cur)} → {nice(end)}"}


def timeline_ctx(con, today: date, months: int) -> dict:
    """Línea de tiempo: proyectos, planes de estudio y objetivos como barras con avance, más una pista de fechas clave (familia, festivos, vencimientos)."""
    end = today + timedelta(days=30 * months)
    start = today - timedelta(days=14)
    rows = []
    for p in con.execute("SELECT id, name, status, due_date, created_at FROM projects WHERE status IN ('active','blocked','paused') ORDER BY name"):
        t = con.execute(f"SELECT MIN(COALESCE(scheduled_date, due_date)) a, MAX(COALESCE(due_date, scheduled_date)) b, "
                        f"SUM(status='done') d, SUM(status IN {OPEN}) o FROM tasks WHERE project_id=? AND parent_id IS NULL", (p["id"],)).fetchone()
        a = min(x for x in (t["a"], p["created_at"][:10] if p["created_at"] else None) if x) if (t["a"] or p["created_at"]) else None
        b = max((x for x in (t["b"], p["due_date"]) if x), default=None) or (date.fromisoformat(a) + timedelta(days=14)).isoformat() if a else None
        total = (t["d"] or 0) + (t["o"] or 0)
        if a and b:
            rows.append({"label": p["name"], "start": a, "end": max(a, b), "percent": round(100 * (t["d"] or 0) / total) if total else 0, "href": "/projects",
                         "cls": "bad" if p["status"] == "blocked" else ""})
    for pl in con.execute("SELECT id, title, start_date, end_date FROM plans WHERE status='active'"):
        pr = plans.progress(con, pl["id"])
        rows.append({"label": "📚 " + pl["title"], "start": pl["start_date"], "end": pl["end_date"], "percent": pr["percent"], "href": f"/plans/{pl['id']}", "cls": "ok"})
    for g in con.execute("SELECT title, horizon, due_date, created_at FROM goals WHERE status='active' AND due_date IS NOT NULL"):
        rows.append({"label": "🎯 " + g["title"], "start": g["created_at"][:10], "end": g["due_date"], "percent": 0, "href": "/goals", "cls": ""})
    rows = [r for r in rows if r["start"] and r["end"] and r["end"] >= start.isoformat() and r["start"] <= end.isoformat()]
    rows.sort(key=lambda r: (r["start"], r["label"]))
    g = plans.gantt(rows + [{"label": "", "start": start.isoformat(), "end": end.isoformat(), "percent": 0}], today.isoformat())
    g["rows"] = [r for r in g["rows"] if r["label"]]
    # pista de fechas clave
    key = agenda.items(con, today, end, {"familia", "festivo", "documento", "cobro", "vence"})
    return {"gantt": g, "key": [k for k in key if k["kind"] != "festivo"][:40], "months": months}


def task_view(con, view: str, project: str = "", tag: str = "", today: date | None = None) -> dict:
    """Listas inteligentes (Things/Todoist): Próximo, Cualquier momento, Algún día, Tablero, Matriz, Todas, Completadas."""
    today = today or date.today()
    iso = today.isoformat()
    where, args = [f"t.status IN {OPEN}", "t.parent_id IS NULL"], []
    if project:
        where.append("p.name=?")
        args.append(project)
    if tag:
        where.append("(',' || COALESCE(t.tags,'') || ',') LIKE ?")
        args.append(f"%,{tag.lower().lstrip('#')},%")
    base = ("SELECT t.id, t.title, t.status, t.priority, t.due_date, t.scheduled_date, t.start_time, t.est_minutes, t.entity, t.recurrence, t.tags, t.someday, p.name AS project, "
            "(SELECT COUNT(*) FROM tasks s WHERE s.parent_id=t.id AND s.status='done') AS sub_done, (SELECT COUNT(*) FROM tasks s WHERE s.parent_id=t.id AND s.status!='dropped') AS sub_total "
            "FROM tasks t LEFT JOIN projects p ON p.id=t.project_id WHERE ")
    order = " ORDER BY t.priority, COALESCE(t.due_date,'9999'), t.id LIMIT 400"
    out: dict = {"view": view}
    if view == "completadas":
        out["items"] = [dict(r) for r in con.execute(
            "SELECT t.id, t.title, t.done_at, t.priority, t.due_date, t.est_minutes, t.actual_minutes, p.name AS project FROM tasks t LEFT JOIN projects p ON p.id=t.project_id "
            "WHERE t.status='done' AND t.parent_id IS NULL ORDER BY t.done_at DESC LIMIT 200")]
        return out
    rows = [dict(r) for r in con.execute(base + " AND ".join(where) + order, args)]
    out["count"] = len(rows)
    if view == "proximo":
        groups: dict[str, list] = {}
        for r in rows:
            if r["someday"]:
                continue
            day = r["scheduled_date"] or r["due_date"]
            if day and day >= iso:
                groups.setdefault(day, []).append(r)
        out["groups"] = [{"date": d, "label": nice(date.fromisoformat(d)), "items": groups[d], "load": planner.load(con, date.fromisoformat(d))} for d in sorted(groups)[:21]]
        out["overdue"] = [r for r in rows if not r["someday"] and (r["scheduled_date"] or r["due_date"] or "9999") < iso]
        out["undated"] = [r for r in rows if not r["someday"] and not r["due_date"] and not r["scheduled_date"]]
    elif view == "cualquier":
        out["items"] = [r for r in rows if not r["someday"] and not r["due_date"] and not r["scheduled_date"]]
    elif view == "algun-dia":
        out["items"] = [r for r in rows if r["someday"]]
    elif view == "tablero":
        cols = {"todo": [], "doing": [], "blocked": []}
        for r in rows:
            cols[r["status"]].append(r)
        done = [dict(r) for r in con.execute("SELECT t.id, t.title, t.priority, t.due_date, p.name AS project FROM tasks t LEFT JOIN projects p ON p.id=t.project_id "
                                             "WHERE t.status='done' AND t.parent_id IS NULL AND t.done_at >= date('now','-7 day') ORDER BY t.done_at DESC LIMIT 30")]
        out["cols"] = [("todo", "Por hacer", cols["todo"]), ("doing", "En curso", cols["doing"]), ("blocked", "Bloqueadas", cols["blocked"]), ("done", "Hechas (7 días)", done)]
    elif view == "matriz":
        soon = (today + timedelta(days=3)).isoformat()
        q = {"q1": [], "q2": [], "q3": [], "q4": []}  # urgente+importante · importante · urgente · resto
        for r in rows:
            if r["someday"]:
                continue
            urgent = bool(r["due_date"] and r["due_date"] <= soon)
            important = r["priority"] <= 2
            q["q1" if urgent and important else "q2" if important else "q3" if urgent else "q4"].append(r)
        out["quads"] = [("q1", "Hacer ya", "Urgente e importante", q["q1"]), ("q2", "Planificar", "Importante, no urgente", q["q2"]),
                        ("q3", "Delegar o agilizar", "Urgente, menos importante", q["q3"]), ("q4", "Eliminar o dejar para después", "Ni urgente ni importante", q["q4"])]
    else:
        out["items"] = rows
    return out
