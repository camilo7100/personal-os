"""Hábitos, personas/compromisos, documentos con vencimiento y suscripciones (vida diaria y administrativa)."""
from datetime import date, timedelta

import typer

from . import dates, db
from .util import JSON, emit

habit_app = typer.Typer(help="Hábitos", no_args_is_help=True)
person_app = typer.Typer(help="Personas y compromisos", no_args_is_help=True)
doc_app = typer.Typer(help="Documentos y vencimientos", no_args_is_help=True)
sub_app = typer.Typer(help="Suscripciones y pagos recurrentes", no_args_is_help=True)


# ---------- funciones compartidas (CLI y portal) ----------
def add_habit(con, name: str, cadence: str = "daily", project: str | None = None) -> None:
    from . import services
    con.execute("INSERT OR IGNORE INTO habits(name, cadence, project_id) VALUES (?,?,?)", (name.strip(), cadence, services.project_id(con, project)))
    db.audit(con, "habit.add", "WRITE", name)


def toggle_habit(con, habit_id: int, day: str | None = None) -> bool:
    """Marca/desmarca el hábito en el día. Devuelve True si queda marcado."""
    day = day or date.today().isoformat()
    if con.execute("SELECT 1 FROM habit_log WHERE habit_id=? AND day=?", (habit_id, day)).fetchone():
        con.execute("DELETE FROM habit_log WHERE habit_id=? AND day=?", (habit_id, day))
        on = False
    else:
        con.execute("INSERT INTO habit_log(habit_id, day) VALUES (?,?)", (habit_id, day))
        on = True
    db.audit(con, "habit.toggle", "WRITE", f"habit:{habit_id}", detail=f"{day} {'on' if on else 'off'}")
    return on


def add_person(con, name: str, relation: str = "", birthday: str | None = None, email: str = "", notes: str = "", circle: str = "") -> int:
    if circle and circle not in dates.CIRCLES:
        raise ValueError(f"círculo inválido: {circle} (usa {', '.join(dates.CIRCLES)})")
    cur = con.execute("INSERT INTO people(name, relation, birthday, email, notes, circle) VALUES (?,?,?,?,?,?)",
                      (name.strip(), relation, birthday or None, email, notes, circle or "otros"))
    db.audit(con, "person.add", "WRITE", f"person:{cur.lastrowid}", detail=name)
    return cur.lastrowid


def add_doc(con, name: str, expires: str | None = None, kind: str = "", entity: str = "personal", notes: str = "") -> int:
    cur = con.execute("INSERT INTO documents(name, kind, expires_on, entity, notes) VALUES (?,?,?,?,?)",
                      (name.strip(), kind, expires or None, entity, notes))
    db.audit(con, "doc.add", "WRITE", f"document:{cur.lastrowid}", detail=name)
    return cur.lastrowid


def add_sub(con, name: str, amount: float, cycle: str = "monthly", next_date: str | None = None, currency: str = "COP",
            entity: str = "personal") -> int:
    cur = con.execute("INSERT INTO subscriptions(name, amount, cycle, next_date, currency, entity) VALUES (?,?,?,?,?,?)",
                      (name.strip(), amount, cycle, next_date or None, currency, entity))
    db.audit(con, "sub.add", "WRITE", f"subscription:{cur.lastrowid}", detail=name)
    return cur.lastrowid


# ---------- hábitos ----------
@habit_app.command("add")
def habit_add(name: str, cadence: str = "daily", project: str = typer.Option(None, help="Proyecto al que pertenece")) -> None:
    with db.session() as con:
        add_habit(con, name, cadence, project)
    typer.echo("ok")


@habit_app.command("check")
def habit_check(name: str, day: str = typer.Option(None, help="YYYY-MM-DD (por defecto hoy)")) -> None:
    day = day or date.today().isoformat()
    with db.session() as con:
        h = con.execute("SELECT id FROM habits WHERE name=? AND active=1", (name,)).fetchone()
        if not h:
            raise typer.BadParameter(f"hábito inexistente: {name}")
        con.execute("INSERT OR IGNORE INTO habit_log(habit_id, day) VALUES (?,?)", (h["id"], day))
        db.audit(con, "habit.check", "WRITE", name, detail=day)
    typer.echo(f"{name} ✓ {day}")


def habit_status(con, today: str | None = None) -> list[dict]:
    """Racha actual y si está hecho hoy, por hábito activo."""
    today = today or date.today().isoformat()
    out = []
    for h in con.execute("SELECT h.id, h.name, h.project_id, p.name AS project FROM habits h LEFT JOIN projects p ON p.id=h.project_id WHERE h.active=1 ORDER BY h.name").fetchall():
        days = {r["day"] for r in con.execute("SELECT day FROM habit_log WHERE habit_id=?", (h["id"],))}
        d, streak = date.fromisoformat(today), 0
        if d.isoformat() not in days:
            d -= timedelta(days=1)  # la racha no se rompe hasta que termina el día
        while d.isoformat() in days:
            streak += 1
            d -= timedelta(days=1)
        out.append({"id": h["id"], "name": h["name"], "done_today": today in days, "streak": streak, "project_id": h["project_id"], "project": h["project"]})
    return out


@habit_app.command("list")
def habit_list(as_json: bool = JSON) -> None:
    with db.session() as con:
        emit(habit_status(con), as_json, ["name", "done_today", "streak"])


# ---------- personas ----------
@person_app.command("add")
def person_add(name: str, relation: str = "", birthday: str = typer.Option(None, help="MM-DD"), email: str = "",
               notes: str = "", circle: str = typer.Option("otros", help="familia | pareja | otros (familia y pareja avisan a 30 y 15 días)")) -> None:
    with db.session() as con:
        typer.echo(f"person #{add_person(con, name, relation, birthday, email, notes, circle)}")


@person_app.command("set")
def person_set(person_id: int, name: str = typer.Option(None), relation: str = typer.Option(None), birthday: str = typer.Option(None, help="MM-DD"),
               circle: str = typer.Option(None, help="familia | pareja | otros")) -> None:
    """Corrige los datos de una persona."""
    if circle and circle not in dates.CIRCLES:
        raise typer.BadParameter(f"círculo inválido: {circle}")
    if birthday:
        dates.occurrence(birthday, 2027)
    changes = {k: v for k, v in dict(name=name, relation=relation, birthday=birthday, circle=circle).items() if v is not None}
    if not changes:
        raise typer.BadParameter("indica al menos un campo")
    with db.session() as con:
        con.execute(f"UPDATE people SET {', '.join(f'{k}=?' for k in changes)} WHERE id=?", (*changes.values(), person_id))
        db.audit(con, "person.set", "WRITE", f"person:{person_id}", detail=str(changes))
    typer.echo("ok")


@person_app.command("contacted")
def person_contacted(person_id: int) -> None:
    with db.session() as con:
        con.execute("UPDATE people SET last_contact=? WHERE id=?", (date.today().isoformat(), person_id))
        db.audit(con, "person.contacted", "WRITE", f"person:{person_id}")
    typer.echo("ok")


@person_app.command("list")
def person_list(as_json: bool = JSON) -> None:
    with db.session() as con:
        emit(con.execute("SELECT id, name, relation, circle, birthday, last_contact FROM people ORDER BY COALESCE(circle,'otros')='otros', name").fetchall(), as_json)


# ---------- documentos ----------
@doc_app.command("add")
def doc_add(name: str, expires: str = typer.Option(None, help="YYYY-MM-DD"), kind: str = "", entity: str = "personal",
            notes: str = "") -> None:
    with db.session() as con:
        typer.echo(f"document #{add_doc(con, name, expires, kind, entity, notes)}")


@doc_app.command("list")
def doc_list(as_json: bool = JSON) -> None:
    with db.session() as con:
        emit(con.execute("SELECT id, name, kind, entity, expires_on FROM documents ORDER BY COALESCE(expires_on,'9999')").fetchall(), as_json)


# ---------- suscripciones ----------
@sub_app.command("add")
def sub_add(name: str, amount: float, cycle: str = "monthly", next_date: str = typer.Option(None, help="YYYY-MM-DD"),
            currency: str = "COP", entity: str = "personal") -> None:
    with db.session() as con:
        typer.echo(f"subscription #{add_sub(con, name, amount, cycle, next_date, currency, entity)}")


@sub_app.command("list")
def sub_list(as_json: bool = JSON) -> None:
    with db.session() as con:
        emit(con.execute("SELECT id, name, amount, currency, cycle, next_date, entity FROM subscriptions WHERE active=1 ORDER BY next_date").fetchall(), as_json)


def monthly_cost(con) -> float:
    factor = {"monthly": 1, "yearly": 1 / 12, "weekly": 52 / 12}
    return round(sum(r["amount"] * factor[r["cycle"]] for r in con.execute(
        "SELECT amount, cycle FROM subscriptions WHERE active=1")), 2)


def upcoming(con, days: int = 30) -> dict:
    """Vencimientos, renovaciones y cumpleaños próximos."""
    today = date.today()
    limit = (today + timedelta(days=days)).isoformat()
    docs = [dict(r) for r in con.execute(
        "SELECT name, expires_on FROM documents WHERE expires_on IS NOT NULL AND expires_on <= ? ORDER BY expires_on", (limit,))]
    subs = [dict(r) for r in con.execute(
        "SELECT name, amount, next_date FROM subscriptions WHERE active=1 AND next_date IS NOT NULL AND next_date <= ? ORDER BY next_date", (limit,))]
    bdays = []
    for p in con.execute("SELECT name, birthday FROM people WHERE birthday IS NOT NULL AND COALESCE(circle,'otros')='otros'"):
        try:
            m, d = map(int, p["birthday"].split("-"))
            nxt = date(today.year, m, d)
            if nxt < today:
                nxt = date(today.year + 1, m, d)
            if (nxt - today).days <= days:
                bdays.append({"name": p["name"], "date": nxt.isoformat()})
        except ValueError:
            continue
    return {"documents": docs, "subscriptions": subs, "birthdays": sorted(bdays, key=lambda b: b["date"]),
            "family": dates.circle_events(con, today, max(days, 45)), "holidays": dates.holidays_between(today, min(days, 21))}
