"""CLI `pos`: única puerta de entrada determinística al sistema (Claude Code, jobs y portal comparten `services`)."""
import functools
import json

import typer

from . import db, services
from .util import JSON, emit

app = typer.Typer(help="PERSONAL_OS — sistema operativo personal", no_args_is_help=True)
inbox_app = typer.Typer(help="Captura universal", no_args_is_help=True)
task_app = typer.Typer(help="Tareas", no_args_is_help=True)
project_app = typer.Typer(help="Proyectos", no_args_is_help=True)
goal_app = typer.Typer(help="Objetivos", no_args_is_help=True)
audit_app = typer.Typer(help="Auditoría", no_args_is_help=True)
for _name, _sub in (("inbox", inbox_app), ("task", task_app), ("project", project_app), ("goal", goal_app), ("audit", audit_app)):
    app.add_typer(_sub, name=_name)


def _guard(fn):
    """Convierte errores de negocio en mensajes limpios de CLI."""
    @functools.wraps(fn)  # conserva la firma que Typer necesita para construir las opciones
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except (services.NotFound, ValueError) as e:
            raise typer.BadParameter(str(e))
    return wrapper


@app.command()
def init() -> None:
    """Crea/actualiza la base de datos."""
    db.init_db()
    with db.session() as con:
        db.audit(con, "init", "WRITE", str(db.DB_PATH))
    typer.echo(f"Base lista: {db.DB_PATH}")


# ---------- inbox ----------
@inbox_app.command("add")
def inbox_add(text: str, source: str = "terminal") -> None:
    """Captura primero, clasifica después."""
    with db.session() as con:
        typer.echo(f"inbox #{services.add_inbox(con, text, source)}")


@inbox_app.command("list")
def inbox_list(all: bool = False, as_json: bool = JSON) -> None:
    with db.session() as con:
        q = "SELECT id, captured_at, source, status, suggestion, text FROM inbox" + ("" if all else " WHERE status='new'") + " ORDER BY id"
        emit(con.execute(q).fetchall(), as_json, ["id", "captured_at", "source", "text"])


@inbox_app.command("resolve")
@_guard
def inbox_resolve(item_id: int, as_: str = typer.Option(..., "--as", help="task|note|discard"),
                  title: str = typer.Option(None, help="Título (tarea/nota)"), project: str = typer.Option(None),
                  due: str = typer.Option(None, help="YYYY-MM-DD"), prio: int = 3) -> None:
    """Resuelve un ítem del inbox: lo convierte en tarea/nota o lo descarta."""
    with db.session() as con:
        typer.echo(f"inbox #{item_id} -> {services.resolve_inbox(con, item_id, as_, title, project, due, prio)}")


@inbox_app.command("triage")
def inbox_triage() -> None:
    """Deja una sugerencia (tarea/nota/descarte, fecha, proyecto) en cada ítem nuevo."""
    from . import triage
    typer.echo(triage.run())


# ---------- tasks ----------
@task_app.command("add")
@_guard
def task_add(title: str, project: str = typer.Option(None, help="Nombre del proyecto"),
             due: str = typer.Option(None, help="YYYY-MM-DD"), scheduled: str = typer.Option(None, help="YYYY-MM-DD"),
             prio: int = 3, entity: str = "personal", notes: str = "",
             repeat: str = typer.Option(None, help="daily|weekly|biweekly|monthly|quarterly|yearly"),
             tags: str = typer.Option("", help="etiquetas separadas por coma")) -> None:
    with db.session() as con:
        typer.echo(f"task #{services.add_task(con, title, project, due, scheduled, prio, entity, notes, repeat, tags=tags)}")


@task_app.command("list")
def task_list(status: str = "open", tag: str = typer.Option(None, help="filtrar por etiqueta"), as_json: bool = JSON) -> None:
    """status: open (todo/doing/blocked) | todo | doing | blocked | done | all"""
    where = {"open": "t.status IN ('todo','doing','blocked')", "all": "1=1"}.get(
        status, f"t.status='{status}'" if status in ("todo", "doing", "blocked", "done", "dropped") else None)
    if where is None:
        raise typer.BadParameter("status inválido")
    args = ()
    if tag:
        where, args = where + " AND (',' || COALESCE(t.tags,'') || ',') LIKE ?", (f"%,{services.norm_tags(tag)},%",)
    with db.session() as con:
        rows = con.execute(
            f"SELECT t.id, t.status, t.priority AS p, t.due_date AS due, t.entity, p.name AS project, t.tags, t.title "
            f"FROM tasks t LEFT JOIN projects p ON p.id=t.project_id WHERE {where} "
            f"ORDER BY t.priority, COALESCE(t.due_date,'9999'), t.id", args).fetchall()
        emit(rows, as_json, ["id", "status", "p", "due", "entity", "project", "tags", "title"])


@task_app.command("done")
def task_done(task_id: int) -> None:
    with db.session() as con:
        ok, nxt = services.done_task(con, task_id)
    typer.echo(("hecho" + (f"; siguiente ocurrencia: task #{nxt}" if nxt else "")) if ok else "sin cambios")


@task_app.command("move")
def task_move(task_id: int, to: str = typer.Argument(..., help="YYYY-MM-DD"), field: str = typer.Option("due", help="due | scheduled")) -> None:
    """Reprograma una tarea (fecha límite o fecha de trabajo)."""
    with db.session() as con:
        typer.echo("reprogramada" if services.move_task(con, task_id, to, field) else "sin cambios")


@task_app.command("drop")
def task_drop(task_id: int) -> None:
    """Descarta una tarea (queda en la base como 'dropped', no se borra)."""
    with db.session() as con:
        typer.echo("descartada" if services.drop_task(con, task_id) else "sin cambios")


# ---------- projects / goals ----------
@project_app.command("add")
def project_add(name: str, entity: str = "personal", area: str = "", due: str = typer.Option(None), milestone: str = "",
                tags: str = typer.Option("", help="etiquetas separadas por coma")) -> None:
    with db.session() as con:
        typer.echo(f"project #{services.add_project(con, name, entity, area, due, milestone, tags=tags)}")


@project_app.command("list")
def project_list(as_json: bool = JSON) -> None:
    with db.session() as con:
        emit(con.execute("SELECT id, status, entity, area, name, next_milestone, due_date FROM projects WHERE status!='done' ORDER BY status, name").fetchall(), as_json)


@project_app.command("status")
def project_status(project_id: int, status: str = typer.Argument(..., help="active|blocked|paused|done")) -> None:
    with db.session() as con:
        services.set_project_status(con, project_id, status)
    typer.echo("ok")


@goal_app.command("add")
def goal_add(title: str, horizon: str = "year", due: str = typer.Option(None)) -> None:
    with db.session() as con:
        typer.echo(f"goal #{services.add_goal(con, title, horizon, due)}")


@goal_app.command("list")
def goal_list(as_json: bool = JSON) -> None:
    with db.session() as con:
        emit(con.execute("SELECT id, horizon, status, due_date, title FROM goals WHERE status='active' ORDER BY horizon, id").fetchall(), as_json)


# ---------- today ----------
@app.command()
def tags(as_json: bool = JSON) -> None:
    """Etiquetas en uso (tareas abiertas, proyectos, planes y notas)."""
    with db.session() as con:
        emit(services.tag_cloud(con), as_json)


@app.command()
def today(as_json: bool = JSON) -> None:
    """Vista HOY: vencidas, para hoy, inbox, correo pendiente, alertas y jobs fallidos."""
    from . import briefing
    with db.session() as con:
        g = briefing.gather(con)
    keys = ("date", "overdue", "today", "inbox_new", "emails_for_claude", "alerts", "jobs_failed_24h", "habits", "health_warnings")
    if as_json:
        typer.echo(json.dumps({k: g[k] for k in keys}, ensure_ascii=False, indent=2))
        return
    typer.echo(f"== HOY {g['date']} ==")
    for label, key in (("VENCIDAS", "overdue"), ("HOY", "today")):
        typer.echo(f"{label}: {len(g[key])}")
        for r in g[key]:
            typer.echo(f"  #{r['id']} [p{r['p']}] {r['title']}")
    typer.echo(f"INBOX sin clasificar: {g['inbox_new']}   CORREOS para Claude: {g['emails_for_claude']}")
    for h in g["habits"]:
        typer.echo(f"  hábito {'✓' if h['done_today'] else '·'} {h['name']} (racha {h['streak']})")
    for a in g["alerts"]:
        typer.echo(f"  ! {a['level']}: {a['title']}")
    for j in g["jobs_failed_24h"]:
        typer.echo(f"  x job fallido: {j['job']} — {j['message']}")


# ---------- audit ----------
@audit_app.command("tail")
def audit_tail(n: int = 20, as_json: bool = JSON) -> None:
    with db.session() as con:
        emit(con.execute("SELECT ts, actor, permission, action, target, result FROM audit_log ORDER BY id DESC LIMIT ?", (n,)).fetchall(), as_json)


# ---------- módulos registrados ----------
from . import agenda, ai, ask, backup, briefing, calendar_sync, career, coursera, dates, finance, jobs, life, mail, media, money, notes, plans, planner, panel, portal_security, reminders, review, statements, repos, setup, telegram  # noqa: E402

for _name, _mod in (("calendar", calendar_sync), ("start", setup), ("alerts", reminders), ("plan", plans), ("ask", ask), ("portal", portal_security),("backup", backup),("job", jobs), ("mail", mail), ("money", money), ("career", career), ("note", notes),
                    ("repos", repos), ("briefing", briefing), ("ai", ai), ("telegram", telegram), ("media", media), ("date", dates), ("coursera", coursera), ("agenda", agenda), ("planner", planner), ("finance", finance), ("review", review), ("statements", statements), ("panel", panel)):
    app.add_typer(_mod.app, name=_name)
for _name, _sub in (("habit", life.habit_app), ("person", life.person_app), ("doc", life.doc_app), ("sub", life.sub_app)):
    app.add_typer(_sub, name=_name)

if __name__ == "__main__":
    app()
