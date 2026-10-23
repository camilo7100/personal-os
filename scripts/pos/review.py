"""Revisión semanal guiada (GTD / Sunsama): qué hiciste, qué quedó atrasado, qué proyectos se estancaron, qué ya no vale la pena y cómo viene la próxima semana.
Todo determinista; el portal la muestra con acciones de un clic."""
from datetime import date, timedelta

import typer

from . import db, planner
from .util import JSON

app = typer.Typer(help="Revisión semanal", no_args_is_help=True)
OPEN = "('todo','doing','blocked')"


def build(con, today: date | None = None) -> dict:
    today = today or date.today()
    iso = today.isoformat()
    week_ago = (today - timedelta(days=7)).isoformat()
    q = lambda sql, *a: [dict(r) for r in con.execute(sql, a)]
    done = q("SELECT id, title, done_at FROM tasks WHERE status='done' AND parent_id IS NULL AND date(done_at) > date(?) ORDER BY done_at DESC", week_ago)
    overdue = q(f"SELECT id, title, due_date, priority FROM tasks WHERE status IN {OPEN} AND parent_id IS NULL AND due_date < ? ORDER BY due_date", iso)
    # proyectos activos: sin ninguna tarea abierta («¿cuál es el siguiente paso?») o sin movimiento en 14 días
    projects = q("SELECT p.id, p.name, "
                 f"(SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id AND t.status IN {OPEN}) AS open, "
                 "(SELECT MAX(COALESCE(t.done_at, t.created_at)) FROM tasks t WHERE t.project_id=p.id) AS last FROM projects p WHERE p.status='active'")
    no_next = [p for p in projects if p["open"] == 0]
    stalled = [p for p in projects if p["open"] and (p["last"] or "0000")[:10] < (today - timedelta(days=14)).isoformat()]
    old = q(f"SELECT id, title, created_at FROM tasks WHERE status IN {OPEN} AND parent_id IS NULL AND due_date IS NULL AND someday=0 AND date(created_at) < date(?) ORDER BY created_at LIMIT 12",
            (today - timedelta(days=30)).isoformat())
    nxt_monday = today + timedelta(days=7 - today.weekday())
    loads = planner.week_load(con, nxt_monday)
    habits = []
    for h in con.execute("SELECT id, name FROM habits WHERE active=1"):
        n = con.execute("SELECT COUNT(*) FROM habit_log WHERE habit_id=? AND day > ?", (h["id"], week_ago)).fetchone()[0]
        habits.append({"name": h["name"], "days": n})
    focus = con.execute("SELECT COALESCE(SUM(minutes),0) FROM task_time WHERE day > ?", (week_ago,)).fetchone()[0]
    inbox = con.execute("SELECT COUNT(*) FROM inbox WHERE status='new'").fetchone()[0]
    steps = [("Bandeja de entrada en cero", inbox == 0, f"{inbox} por clasificar" if inbox else "vacía"),
             ("Nada atrasado", not overdue, f"{len(overdue)} vencidas" if overdue else "al día"),
             ("Cada proyecto activo tiene siguiente paso", not no_next, ", ".join(p["name"] for p in no_next[:3]) or "todos tienen"),
             ("Próxima semana equilibrada", all(l["state"] != "over" for l in loads), f"{sum(1 for l in loads if l['state'] == 'over')} día(s) sobrecargado(s)")]
    return {"date": iso, "done": done, "overdue": overdue, "no_next": no_next, "stalled": stalled, "old": old, "next_week": loads, "next_monday": nxt_monday.isoformat(),
            "habits": habits, "focus_min": focus, "inbox": inbox, "steps": steps, "score": sum(1 for s in steps if s[1])}


@app.command("show")
def show_cmd(as_json: bool = JSON) -> None:
    """Resumen de la revisión semanal."""
    import json
    with db.session() as con:
        r = build(con)
    if as_json:
        typer.echo(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        return
    typer.echo(f"Hechas en 7 días: {len(r['done'])} · vencidas: {len(r['overdue'])} · enfoque: {r['focus_min']} min · bandeja: {r['inbox']}")
    for name, ok, note in r["steps"]:
        typer.echo(f"{'✔' if ok else '✖'} {name} — {note}")
