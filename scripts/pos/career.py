"""Empleo y carrera: seguimiento de vacantes y postulaciones. Nunca postula por su cuenta (SENSITIVE_ACTION)."""
import typer

from . import db
from .util import JSON, emit

app = typer.Typer(help="Carrera y empleo", no_args_is_help=True)
STATUSES = ("saved", "applied", "interview", "offer", "rejected", "withdrawn")


def add_application(con, company: str, role: str, url: str = "", status: str = "saved", next_step: str = "",
                    next_step_date: str | None = None, notes: str = "") -> int:
    if status not in STATUSES:
        raise ValueError(f"status: {', '.join(STATUSES)}")
    con.execute("INSERT OR IGNORE INTO companies(name) VALUES (?)", (company.strip(),))
    cid = con.execute("SELECT id FROM companies WHERE name=?", (company.strip(),)).fetchone()["id"]
    cur = con.execute(
        "INSERT INTO applications(company_id, role, status, url, next_step, next_step_date, notes) VALUES (?,?,?,?,?,?,?)",
        (cid, role.strip(), status, url, next_step, next_step_date or None, notes))
    db.audit(con, "career.add", "WRITE", f"application:{cur.lastrowid}", detail=f"{company} — {role}")
    return cur.lastrowid


def update_application(con, app_id: int, **fields) -> None:
    fields = {k: (v or None) if k == "next_step_date" else v for k, v in fields.items() if v is not None}
    if fields.get("status") and fields["status"] not in STATUSES:
        raise ValueError(f"status: {', '.join(STATUSES)}")
    if not fields:
        raise ValueError("nada que actualizar")
    if fields.get("status") == "applied":
        con.execute("UPDATE applications SET applied_on=COALESCE(applied_on, date('now')) WHERE id=?", (app_id,))
    con.execute(f"UPDATE applications SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), app_id))
    db.audit(con, "career.update", "WRITE", f"application:{app_id}", detail=str(fields)[:200])


@app.command("add")
def add(company: str, role: str, url: str = "", status: str = "saved", next_step: str = "",
        next_step_date: str = typer.Option(None, help="YYYY-MM-DD"), notes: str = "") -> None:
    """Registra una vacante/postulación (la empresa se crea si no existe)."""
    try:
        with db.session() as con:
            typer.echo(f"application #{add_application(con, company, role, url, status, next_step, next_step_date, notes)}")
    except ValueError as e:
        raise typer.BadParameter(str(e))


@app.command("update")
def update(app_id: int, status: str = typer.Option(None), next_step: str = typer.Option(None),
           next_step_date: str = typer.Option(None), notes: str = typer.Option(None)) -> None:
    try:
        with db.session() as con:
            update_application(con, app_id, status=status, next_step=next_step, next_step_date=next_step_date, notes=notes)
    except ValueError as e:
        raise typer.BadParameter(str(e))
    typer.echo("ok")


def board(con) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT a.id, c.name AS company, a.role, a.status, a.applied_on, a.next_step, a.next_step_date, a.url "
        "FROM applications a JOIN companies c ON c.id=a.company_id WHERE a.status NOT IN ('rejected','withdrawn') "
        "ORDER BY CASE a.status WHEN 'offer' THEN 0 WHEN 'interview' THEN 1 WHEN 'applied' THEN 2 ELSE 3 END, COALESCE(a.next_step_date,'9999')")]


@app.command("board")
def board_cmd(as_json: bool = JSON) -> None:
    with db.session() as con:
        emit(board(con), as_json, ["id", "status", "company", "role", "next_step_date", "next_step"])
