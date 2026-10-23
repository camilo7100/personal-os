"""Operaciones de negocio compartidas por la CLI y el portal (un solo lugar para escribir en la base)."""
import sqlite3
from datetime import date

from . import db


class NotFound(ValueError):
    pass


def project_id(con: sqlite3.Connection, name: str | None) -> int | None:
    if not name:
        return None
    r = con.execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()
    if not r:
        raise NotFound(f"proyecto inexistente: {name}")
    return r["id"]


def add_inbox(con, text: str, source: str = "terminal", actor: str | None = None) -> int:
    cur = con.execute("INSERT INTO inbox(text, source) VALUES (?,?)", (text.strip(), source))
    db.audit(con, "inbox.add", "WRITE", f"inbox:{cur.lastrowid}", actor=actor)
    return cur.lastrowid


def norm_tags(raw: str | None) -> str:
    """'#Coursera, IA  datos' → 'coursera,datos,ia' (minúsculas, sin repetidos, orden estable)."""
    import re
    return ",".join(sorted({t.strip().lstrip("#").lower() for t in re.split(r"[,\s]+", raw or "") if t.strip().lstrip("#")}))


def tag_cloud(con) -> list[dict]:
    """Etiquetas en uso con su conteo, sumando tareas abiertas, proyectos, planes y notas."""
    counts: dict[str, int] = {}
    for sql in ("SELECT tags FROM tasks WHERE status IN ('todo','doing','blocked')", "SELECT tags FROM projects WHERE status!='done'",
                "SELECT tags FROM plans WHERE status!='done'", "SELECT tags FROM notes"):
        for r in con.execute(sql):
            for t in (r[0] or "").replace(" ", "").split(","):
                if t:
                    counts[t] = counts.get(t, 0) + 1
    return [{"tag": t, "n": n} for t, n in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]


def add_task(con, title: str, project: str | None = None, due: str | None = None, scheduled: str | None = None,
             prio: int = 3, entity: str = "personal", notes: str = "", repeat: str | None = None,
             source: str = "manual", actor: str | None = None, tags: str = "") -> int:
    cur = con.execute(
        "INSERT INTO tasks(title, notes, priority, project_id, entity, due_date, scheduled_date, recurrence, source, tags) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (title.strip(), notes, prio, project_id(con, project), entity, due or None, scheduled or None, repeat or None, source, norm_tags(tags)))
    db.audit(con, "task.add", "WRITE", f"task:{cur.lastrowid}", detail=title, actor=actor)
    return cur.lastrowid


def done_task(con, task_id: int, actor: str | None = None) -> tuple[bool, int | None]:
    exists = con.execute("SELECT 1 FROM tasks WHERE id=? AND status!='done'", (task_id,)).fetchone()
    nxt = db.complete_task(con, task_id)
    db.audit(con, "task.done", "WRITE", f"task:{task_id}", "ok" if exists else "noop", actor=actor)
    return bool(exists), nxt


def drop_task(con, task_id: int, actor: str | None = None) -> bool:
    cur = con.execute("UPDATE tasks SET status='dropped' WHERE id=? AND status NOT IN ('done','dropped')", (task_id,))
    db.audit(con, "task.drop", "WRITE", f"task:{task_id}", "ok" if cur.rowcount else "noop", actor=actor)
    return bool(cur.rowcount)


def move_task(con, task_id: int, to: str, field: str = "due", actor: str | None = None) -> bool:
    col = {"due": "due_date", "scheduled": "scheduled_date"}[field]
    cur = con.execute(f"UPDATE tasks SET {col}=? WHERE id=? AND status NOT IN ('done','dropped')", (to or None, task_id))
    db.audit(con, "task.move", "WRITE", f"task:{task_id}", "ok" if cur.rowcount else "noop", to, actor=actor)
    return bool(cur.rowcount)


def resolve_inbox(con, item_id: int, kind: str, title: str | None = None, project: str | None = None,
                  due: str | None = None, prio: int = 3, actor: str | None = None) -> str:
    """kind: task | note | discard. Devuelve a qué quedó resuelto (task:N, note:ruta, discard)."""
    row = con.execute("SELECT text FROM inbox WHERE id=? AND status='new'", (item_id,)).fetchone()
    if not row:
        raise NotFound(f"no existe inbox nuevo #{item_id}")
    resolved = kind
    if kind == "task":
        tid = add_task(con, title or row["text"], project=project, due=due, prio=prio, source="inbox", actor=actor)
        resolved = f"task:{tid}"
    elif kind == "note":
        from . import notes
        notes.ensure_vault()
        path = notes.VAULT / "inbox" / f"{date.today().isoformat()}-{notes.slug(title or row['text'])[:60]}.md"
        n = 1
        while path.exists():
            n += 1
            path = path.with_name(f"{path.stem}-{n}.md")
        t = title or row["text"][:80]
        path.write_text(f"---\ntitle: {t}\ntags: []\ncreated: {date.today().isoformat()}\n---\n\n# {t}\n\n{row['text']}\n", encoding="utf-8")
        resolved = f"note:{path.relative_to(notes.VAULT)}"
    elif kind != "discard":
        raise ValueError("kind: task | note | discard")
    con.execute("UPDATE inbox SET status=?, resolved_as=? WHERE id=?", ("discarded" if kind == "discard" else "processed", resolved, item_id))
    db.audit(con, "inbox.resolve", "WRITE", f"inbox:{item_id}", detail=resolved, actor=actor)
    return resolved


def add_project(con, name: str, entity: str = "personal", area: str = "", due: str | None = None, milestone: str = "",
                actor: str | None = None, tags: str = "") -> int:
    cur = con.execute("INSERT INTO projects(name, entity, area, due_date, next_milestone, tags) VALUES (?,?,?,?,?,?)",
                      (name.strip(), entity, area, due or None, milestone, norm_tags(tags)))
    db.audit(con, "project.add", "WRITE", f"project:{cur.lastrowid}", detail=name, actor=actor)
    return cur.lastrowid


def set_project_status(con, project_id_: int, status: str, actor: str | None = None) -> None:
    con.execute("UPDATE projects SET status=? WHERE id=?", (status, project_id_))
    db.audit(con, "project.status", "WRITE", f"project:{project_id_}", detail=status, actor=actor)


def add_goal(con, title: str, horizon: str = "year", due: str | None = None, actor: str | None = None) -> int:
    cur = con.execute("INSERT INTO goals(title, horizon, due_date) VALUES (?,?,?)", (title.strip(), horizon, due or None))
    db.audit(con, "goal.add", "WRITE", f"goal:{cur.lastrowid}", detail=title, actor=actor)
    return cur.lastrowid


def set_goal_status(con, goal_id: int, status: str, actor: str | None = None) -> None:
    con.execute("UPDATE goals SET status=? WHERE id=?", (status, goal_id))
    db.audit(con, "goal.status", "WRITE", f"goal:{goal_id}", detail=status, actor=actor)


# ---------- gestor de tareas: captura rápida, edición, subtareas, tiempo ----------
TASK_FIELDS = {"title", "notes", "priority", "due_date", "scheduled_date", "start_time", "est_minutes", "tags", "recurrence", "someday", "status", "entity", "parent_id"}


def add_quick(con, text: str, source: str = "portal", actor: str | None = None, default_project: str | None = None) -> tuple[int, dict]:
    """Una línea en lenguaje natural → tarea completa (fecha, hora, duración, prioridad, proyecto, etiquetas, repetición). Devuelve (id, lo entendido)."""
    from . import quick
    names = [r["name"] for r in con.execute("SELECT name FROM projects WHERE status!='done'")]
    q = quick.parse_quick(text, names)
    when = q["due"]
    cur = con.execute(
        "INSERT INTO tasks(title, priority, project_id, due_date, scheduled_date, start_time, est_minutes, recurrence, source, tags, someday) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (q["title"], q["priority"] or 3, project_id(con, q["project"] or default_project), None if q["someday"] else when, when if q["time"] else None,
         q["time"], q["est_minutes"], q["recurrence"], source, ",".join(q["tags"]), 1 if q["someday"] else 0))
    db.audit(con, "task.add", "WRITE", f"task:{cur.lastrowid}", detail=q["title"], actor=actor)
    return cur.lastrowid, q


def update_task(con, task_id: int, actor: str | None = None, **fields) -> bool:
    """Edita cualquier campo de una tarea abierta (project por nombre)."""
    sets, args = [], []
    if "project" in fields:
        sets.append("project_id=?")
        args.append(project_id(con, fields.pop("project") or None))
    for k, v in fields.items():
        if k not in TASK_FIELDS:
            raise ValueError(f"campo no editable: {k}")
        if k == "tags":
            v = norm_tags(v)
        if k == "priority" and int(v) not in (1, 2, 3, 4):
            raise ValueError("prioridad: 1 a 4")
        if k in ("due_date", "scheduled_date", "start_time", "recurrence", "est_minutes", "parent_id", "notes") and v in ("", None):
            v = None
        sets.append(f"{k}=?")
        args.append(v)
    if not sets:
        return False
    cur = con.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=? AND status NOT IN ('done','dropped')", (*args, task_id))
    db.audit(con, "task.update", "WRITE", f"task:{task_id}", "ok" if cur.rowcount else "noop", ",".join(fields) or "project", actor=actor)
    return bool(cur.rowcount)


def schedule_task(con, task_id: int, day: str, start_time: str | None, est_minutes: int | None = None, actor: str | None = None) -> bool:
    """Pone la tarea en el calendario: día y hora de trabajo (la fecha límite no cambia, salvo que no tuviera)."""
    row = con.execute("SELECT due_date, est_minutes FROM tasks WHERE id=? AND status NOT IN ('done','dropped')", (task_id,)).fetchone()
    if not row:
        return False
    con.execute("UPDATE tasks SET scheduled_date=?, start_time=?, est_minutes=COALESCE(?, est_minutes), someday=0, due_date=COALESCE(due_date, ?) WHERE id=?",
                (day, start_time or None, est_minutes, day, task_id))
    db.audit(con, "task.schedule", "WRITE", f"task:{task_id}", detail=f"{day} {start_time or ''}", actor=actor)
    return True


def unschedule_task(con, task_id: int, actor: str | None = None) -> bool:
    cur = con.execute("UPDATE tasks SET scheduled_date=NULL, start_time=NULL WHERE id=? AND status NOT IN ('done','dropped')", (task_id,))
    db.audit(con, "task.unschedule", "WRITE", f"task:{task_id}", actor=actor)
    return bool(cur.rowcount)


def add_subtask(con, parent_id: int, title: str, actor: str | None = None) -> int:
    p = con.execute("SELECT project_id, entity, due_date FROM tasks WHERE id=?", (parent_id,)).fetchone()
    if not p:
        raise NotFound(f"tarea inexistente: {parent_id}")
    cur = con.execute("INSERT INTO tasks(title, project_id, entity, due_date, parent_id, source) VALUES (?,?,?,?,?,?)",
                      (title.strip(), p["project_id"], p["entity"], p["due_date"], parent_id, "subtask"))
    db.audit(con, "task.subtask", "WRITE", f"task:{cur.lastrowid}", detail=title, actor=actor)
    return cur.lastrowid


def subtasks(con, parent_id: int) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT id, title, status FROM tasks WHERE parent_id=? AND status!='dropped' ORDER BY id", (parent_id,))]


def log_focus(con, task_id: int, minutes: int, actor: str | None = None) -> None:
    """Suma tiempo enfocado (cronómetro) a una tarea."""
    if minutes <= 0:
        return
    con.execute("INSERT INTO task_time(task_id, day, minutes) VALUES (?,?,?)", (task_id, date.today().isoformat(), minutes))
    con.execute("UPDATE tasks SET actual_minutes=actual_minutes+? WHERE id=?", (minutes, task_id))
    db.audit(con, "task.focus", "WRITE", f"task:{task_id}", detail=f"{minutes} min", actor=actor)


def done_by_day(con, days: int = 14, today: date | None = None) -> list[dict]:
    """Tareas completadas por día (para la gráfica de productividad y la racha)."""
    from datetime import timedelta
    today = today or date.today()
    got = {r["d"]: r["n"] for r in con.execute("SELECT date(done_at,'localtime') AS d, COUNT(*) AS n FROM tasks WHERE status='done' AND done_at >= date(?, ?) GROUP BY d", (today.isoformat(), f"-{days} day"))}
    return [{"day": (today - timedelta(days=i)).isoformat(), "n": got.get((today - timedelta(days=i)).isoformat(), 0)} for i in range(days - 1, -1, -1)]


def task_row(con, task_id: int) -> dict | None:
    r = con.execute("SELECT t.*, p.name AS project FROM tasks t LEFT JOIN projects p ON p.id=t.project_id WHERE t.id=?", (task_id,)).fetchone()
    return dict(r) if r else None
