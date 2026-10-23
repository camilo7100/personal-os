"""Tareas y sesiones de la maestría desde el calendario de Coursera. Claude lee ese calendario con el conector de Google Calendar
(los jobs no tienen credenciales de Google) y entrega los eventos en un JSON. Coursera cambia poco (cada ~2 meses): NO se revisa a diario;
cada 30 días Claude compara y PROPONE los cambios (alerta + /coursera si|no) y solo se aplican si el usuario acepta."""
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import typer

from . import db

app = typer.Typer(help="Sincroniza tareas con el calendario de Coursera", no_args_is_help=True)
SOURCE = "coursera"
PROJECT = "Maestría MIAD (Uniandes)"
BOGOTA = timezone(timedelta(hours=-5))


def _local(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(BOGOTA)


def parse(ev: dict) -> dict | None:
    """Evento de Coursera → {uid, title, due, notes, tags}. Devuelve None si no es una entrega ni una sesión."""
    summary = (ev.get("summary") or "").strip()
    desc = (ev.get("description") or "").replace("<br>", "\n")
    start = _local(ev["start"])
    end = _local(ev.get("end") or ev["start"])
    if summary.startswith("Assignment Due"):
        course = summary.split("--", 1)[-1].strip()
        items = [re.sub(r"\s+", " ", m).strip() for m in re.findall(r"^\s*-\s+(.+)$", desc, re.M)]
        url = (re.search(r"https?://\S+", desc) or [None])[0]
        due = end.date()  # cierran a las 23:59 hora de Bogotá
        return dict(uid=ev["id"], title=f"📚 {course}: " + ("; ".join(items) if items else "entregas de la semana"), due=due.isoformat(),
                    notes=f"Cierra {end:%Y-%m-%d %H:%M} (Bogotá).\n" + "\n".join(f"- {i}" for i in items) + (f"\n{url}" if url else ""), tags="maestria,coursera")
    if "sincr" in summary.lower():
        course = summary.split(" - ")[-1].strip() if " - " in summary else summary
        url = (re.search(r"https?://\S+", desc) or [None])[0]
        return dict(uid=ev["id"], title=f"🎥 {course}: sesión sincrónica {start:%H:%M}", due=start.date().isoformat(),
                    notes=f"{start:%Y-%m-%d %H:%M}–{end:%H:%M} (Bogotá)." + (f"\nZoom: {url}" if url else ""), tags="maestria,zoom")
    return None


def _project_id(con) -> int:
    r = con.execute("SELECT id FROM projects WHERE name=?", (PROJECT,)).fetchone()
    if r:
        return r["id"]
    return con.execute("INSERT INTO projects(name, area, tags) VALUES (?,?,?)", (PROJECT, "estudio", "maestria,coursera")).lastrowid


def ingest(con, events: list[dict], today: date | None = None, window_from: str | None = None, apply: bool = True) -> dict:
    """Crea/mueve/descarta tareas según el calendario. Con apply=False no toca nada y solo devuelve los cambios que haría (`changes`).
    `window_from`: los eventos ya importados desde esa fecha que ya no vienen en la lista se descartan."""
    today = today or date.today()
    stats = dict(created=0, moved=0, dropped=0, unchanged=0, changes=[])
    seen = set()
    for ev in events:
        p = parse(ev)
        if not p:
            continue
        seen.add(p["uid"])
        if p["due"] < today.isoformat():
            continue  # lo pasado no genera tareas nuevas
        row = con.execute("SELECT task_id, due FROM external_items WHERE uid=?", (p["uid"],)).fetchone()
        if not row:
            stats["changes"].append(f"NUEVA {p['due']}: {p['title']}")
            stats["created"] += 1
            if apply:
                tid = con.execute("INSERT INTO tasks(title, notes, priority, project_id, due_date, scheduled_date, source, tags) VALUES (?,?,?,?,?,?,?,?)",
                                  (p["title"], p["notes"], 2, _project_id(con), p["due"], p["due"], SOURCE, p["tags"])).lastrowid
                con.execute("INSERT INTO external_items(uid, source, task_id, due) VALUES (?,?,?,?)", (p["uid"], SOURCE, tid, p["due"]))
                db.audit(con, "coursera.task", "WRITE", f"task:{tid}", detail=p["title"], actor="job")
        elif row["due"] != p["due"]:
            stats["changes"].append(f"MOVIDA {row['due']} → {p['due']}: {p['title']}")
            stats["moved"] += 1
            if apply:
                con.execute("UPDATE tasks SET due_date=?, scheduled_date=?, title=?, notes=? WHERE id=? AND status IN ('todo','doing','blocked')",
                            (p["due"], p["due"], p["title"], p["notes"], row["task_id"]))
                con.execute("UPDATE external_items SET due=?, updated_at=datetime('now') WHERE uid=?", (p["due"], p["uid"]))
                db.audit(con, "coursera.moved", "WRITE", f"task:{row['task_id']}", detail=f"{row['due']} → {p['due']}", actor="job")
        else:
            stats["unchanged"] += 1
    if window_from:
        for r in con.execute("SELECT e.uid, e.task_id, e.due, t.title FROM external_items e JOIN tasks t ON t.id=e.task_id "
                             "WHERE e.source=? AND e.due >= ? AND t.status IN ('todo','doing','blocked')", (SOURCE, window_from)).fetchall():
            if r["uid"] not in seen:
                stats["changes"].append(f"YA NO ESTÁ EN EL CALENDARIO {r['due']}: {r['title']}")
                stats["dropped"] += 1
                if apply:
                    con.execute("UPDATE tasks SET status='dropped' WHERE id=?", (r["task_id"],))
                    con.execute("DELETE FROM external_items WHERE uid=?", (r["uid"],))
                    db.audit(con, "coursera.dropped", "WRITE", f"task:{r['task_id']}", detail="el evento ya no está en el calendario", actor="job")
    return stats


# ---------- revisión periódica: Claude propone, el usuario decide ----------
def check_days() -> int:
    from . import reminders
    return int(reminders.cfg().get("coursera_check_days", 30))


def _meta(con, key: str):
    r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None


def _set_meta(con, key: str, value: str | None) -> None:
    if value is None:
        con.execute("DELETE FROM meta WHERE key=?", (key,))
    else:
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))


def is_due(con, today: date | None = None) -> bool:
    last = _meta(con, "coursera:last_check")
    return not last or (today or date.today()) - date.fromisoformat(last) >= timedelta(days=check_days())


def propose(con, events: list[dict], window_from: str, today: date | None = None) -> dict:
    """Compara el calendario con las tareas SIN cambiar nada. Si hay diferencias, guarda la propuesta y avisa para que el usuario decida."""
    today = today or date.today()
    s = ingest(con, events, today, window_from, apply=False)
    _set_meta(con, "coursera:last_check", today.isoformat())
    if s["changes"]:
        _set_meta(con, "coursera:proposal", json.dumps({"events": events, "window_from": window_from, "changes": s["changes"]}, ensure_ascii=False))
        con.execute("INSERT OR IGNORE INTO alerts(level, title, detail, key, source, link, pushed) VALUES (?,?,?,?,?,?,0)",
                    ("IMPORTANT", f"Coursera: {len(s['changes'])} cambio(s) en tu calendario",
                     "\n".join(s["changes"][:6]) + "\nResponde /coursera si (aplicar) o /coursera no (descartar).",
                     f"coursera-proposal:{today.isoformat()}", "coursera", "/tasks"))
    else:
        _set_meta(con, "coursera:proposal", None)
    return s


def apply_proposal(con, today: date | None = None) -> str:
    raw = _meta(con, "coursera:proposal")
    if not raw:
        return "No hay cambios de Coursera pendientes."
    pr = json.loads(raw)
    s = ingest(con, pr["events"], today, window_from=pr["window_from"], apply=True)
    _set_meta(con, "coursera:proposal", None)
    con.execute("UPDATE alerts SET acknowledged=1 WHERE source='coursera'")
    return f"Coursera actualizado: {s['created']} nuevas, {s['moved']} movidas, {s['dropped']} descartadas."


def discard_proposal(con) -> str:
    _set_meta(con, "coursera:proposal", None)
    con.execute("UPDATE alerts SET acknowledged=1 WHERE source='coursera'")
    return "Cambios de Coursera descartados (tus tareas siguen como estaban)."


@app.command("due")
def due_cmd() -> None:
    """¿Toca revisar Coursera? (cada `coursera_check_days`, 30 por defecto; o si hay una propuesta esperando respuesta)."""
    with db.session() as con:
        pending = bool(_meta(con, "coursera:proposal"))
        typer.echo("propuesta pendiente de tu respuesta" if pending else "REVISAR" if is_due(con) else f"al día (última revisión {_meta(con, 'coursera:last_check')})")


@app.command("propose")
def propose_cmd(file: Path = typer.Argument(..., help="JSON con los eventos del calendario de Coursera"),
                window_from: str = typer.Option(None, help="YYYY-MM-DD (por defecto hoy)")) -> None:
    """Compara sin cambiar nada; si hay diferencias, avisa al usuario (/coursera si|no)."""
    events = json.loads(file.read_text(encoding="utf-8"))
    with db.session() as con:
        s = propose(con, events, window_from or date.today().isoformat())
    from . import notify
    notify.flush_pending()  # envía la alerta al celular (fuera de la transacción anterior)
    typer.echo("Sin cambios en Coursera." if not s["changes"] else f"{len(s['changes'])} cambio(s) propuestos:\n" + "\n".join(s["changes"]))


@app.command("apply")
def apply_cmd() -> None:
    """Aplica la propuesta pendiente (equivale a responder /coursera si)."""
    with db.session() as con:
        typer.echo(apply_proposal(con))


@app.command("discard")
def discard_cmd() -> None:
    """Descarta la propuesta pendiente (equivale a /coursera no)."""
    with db.session() as con:
        typer.echo(discard_proposal(con))


@app.command("ingest")
def ingest_cmd(file: Path = typer.Argument(..., help="JSON: lista de eventos [{id, summary, description, start, end}] del calendario de Coursera"),
               window_from: str = typer.Option(None, help="YYYY-MM-DD: descarta lo importado desde esa fecha que ya no aparezca")) -> None:
    events = json.loads(file.read_text(encoding="utf-8"))
    with db.session() as con:
        s = ingest(con, events, window_from=window_from)
    typer.echo(f"Coursera: {s['created']} nuevas, {s['moved']} movidas, {s['dropped']} descartadas, {s['unchanged']} sin cambios")
