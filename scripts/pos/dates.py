"""Fechas que se repiten cada año (aniversarios, San Valentín, Día de la Madre…), círculo cercano (familia y pareja) y festivos de Colombia.
Una fecha es «MM-DD» (fija) o «N:D:M» (el N-ésimo día de la semana D —lunes=0…domingo=6— del mes M; p. ej. «2:6:5» = segundo domingo de mayo)."""
import json
from datetime import date, timedelta

import typer

from . import db
from .util import JSON, emit

app = typer.Typer(help="Fechas especiales, círculo cercano y festivos", no_args_is_help=True)
CIRCLES = ("familia", "pareja", "otros")
CLOSE = ("familia", "pareja")
CLOSE_LEADS = [30, 15, 0]  # 1 mes antes, 15 días antes y el mismo día (editable en config\alerts.json → close_days)
_DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def occurrence(rule: str, year: int) -> date:
    """Fecha de la regla en un año dado."""
    if ":" in rule:
        n, wd, m = (int(x) for x in rule.split(":"))
        first = date(year, m, 1)
        return first + timedelta(days=(wd - first.weekday()) % 7 + 7 * (n - 1))
    m, d = (int(x) for x in rule.split("-"))
    return date(year, m, d)


def next_occurrence(rule: str, today: date | None = None) -> date:
    today = today or date.today()
    nxt = occurrence(rule, today.year)
    return nxt if nxt >= today else occurrence(rule, today.year + 1)


def rrule(rule: str) -> str:
    """Regla de repetición anual para Google Calendar."""
    if ":" in rule:
        n, wd, m = (int(x) for x in rule.split(":"))
        return f"RRULE:FREQ=YEARLY;BYMONTH={m};BYDAY={n}{['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'][wd]}"
    return "RRULE:FREQ=YEARLY"


def describe(rule: str) -> str:
    if ":" in rule:
        n, wd, m = (int(x) for x in rule.split(":"))
        return f"{n}.º {_DAYS[wd]} del mes {m}"
    return rule


def close_leads(cfg_days: list[int] | None = None) -> list[int]:
    return sorted(cfg_days or CLOSE_LEADS, reverse=True)


def circle_of(con, person_id: int | None) -> str:
    if not person_id:
        return "familia"
    r = con.execute("SELECT circle FROM people WHERE id=?", (person_id,)).fetchone()
    return (r["circle"] if r else None) or "otros"


def add_special(con, title: str, rule: str, person_id: int | None = None, notes: str = "") -> int:
    occurrence(rule, 2027)  # valida el formato
    cur = con.execute("INSERT INTO special_dates(title, rule, person_id, notes) VALUES (?,?,?,?)", (title.strip(), rule, person_id, notes))
    db.audit(con, "date.add", "WRITE", f"special:{cur.lastrowid}", detail=title)
    return cur.lastrowid


def circle_events(con, today: date | None = None, days: int = 400) -> list[dict]:
    """Cumpleaños y fechas especiales del círculo cercano (familia y pareja), ordenados por cercanía."""
    today = today or date.today()
    out = []
    for p in con.execute("SELECT id, name, relation, birthday, circle FROM people WHERE birthday IS NOT NULL AND circle IN ('familia','pareja')"):
        d = next_occurrence(p["birthday"], today)
        out.append(dict(kind="cumpleaños", title=f"Cumpleaños de {p['name']}", who=p["name"], relation=p["relation"], circle=p["circle"],
                        date=d.isoformat(), left=(d - today).days))
    for s in con.execute("SELECT s.id, s.title, s.rule, s.person_id, p.name AS who, p.relation FROM special_dates s LEFT JOIN people p ON p.id=s.person_id"):
        d = next_occurrence(s["rule"], today)
        out.append(dict(kind="fecha especial", title=s["title"], who=s["who"], relation=s["relation"], circle=circle_of(con, s["person_id"]),
                        date=d.isoformat(), left=(d - today).days, id=s["id"]))
    return sorted((e for e in out if e["left"] <= days), key=lambda e: e["date"])


# ---------- festivos de Colombia (Ley Emiliani) ----------
def _easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    L = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * L) // 451
    month, day = divmod(h + L - 7 * m + 114, 31)
    return date(y, month, day + 1)


def _monday(d: date) -> date:
    return d if d.weekday() == 0 else d + timedelta(days=7 - d.weekday())


def holidays(year: int) -> dict[date, str]:
    """Festivos oficiales de Colombia del año (fijos, trasladados al lunes y los de Semana Santa)."""
    e = _easter(year)
    h = {date(year, 1, 1): "Año Nuevo", date(year, 5, 1): "Día del Trabajo", date(year, 7, 20): "Día de la Independencia",
         date(year, 8, 7): "Batalla de Boyacá", date(year, 12, 8): "Inmaculada Concepción", date(year, 12, 25): "Navidad",
         e - timedelta(days=3): "Jueves Santo", e - timedelta(days=2): "Viernes Santo"}
    for (m, d), name in {(1, 6): "Reyes Magos", (3, 19): "San José", (6, 29): "San Pedro y San Pablo", (8, 15): "Asunción de la Virgen",
                         (10, 12): "Día de la Raza", (11, 1): "Todos los Santos", (11, 11): "Independencia de Cartagena"}.items():
        h[_monday(date(year, m, d))] = name
    h[_monday(e + timedelta(days=39))] = "Ascensión del Señor"
    h[_monday(e + timedelta(days=60))] = "Corpus Christi"
    h[_monday(e + timedelta(days=68))] = "Sagrado Corazón"
    return dict(sorted(h.items()))


def holidays_between(start: date, days: int) -> list[dict]:
    end = start + timedelta(days=days)
    out = []
    for y in {start.year, end.year}:
        out += [{"date": d.isoformat(), "name": n} for d, n in holidays(y).items() if start <= d <= end]
    return sorted(out, key=lambda x: x["date"])


# ---------- CLI ----------
@app.command("add")
def add_cmd(title: str, rule: str = typer.Argument(..., help="MM-DD o N:D:M (p. ej. 2:6:5 = 2.º domingo de mayo)"),
            person: str = typer.Option(None, help="Nombre (o parte) de la persona vinculada")) -> None:
    """Añade una fecha anual (aniversario, San Valentín…). Si va ligada a familia/pareja avisa a 30 y 15 días."""
    with db.session() as con:
        pid = None
        if person:
            r = con.execute("SELECT id FROM people WHERE name LIKE ?", (f"%{person}%",)).fetchone()
            if not r:
                raise typer.BadParameter(f"no encuentro a «{person}» (usa `pos person list`)")
            pid = r["id"]
        typer.echo(f"date #{add_special(con, title, rule, pid)}")


@app.command("list")
def list_cmd(as_json: bool = JSON) -> None:
    """Fechas especiales y cumpleaños del círculo cercano con su próxima fecha."""
    with db.session() as con:
        emit(circle_events(con), as_json, ["date", "left", "circle", "title"])


@app.command("holidays")
def holidays_cmd(year: int = typer.Argument(None), as_json: bool = JSON) -> None:
    """Festivos de Colombia."""
    y = year or date.today().year
    rows = [{"date": d.isoformat(), "day": _DAYS[d.weekday()], "name": n} for d, n in holidays(y).items()]
    typer.echo(json.dumps(rows, ensure_ascii=False, indent=2)) if as_json else emit(rows, False)
