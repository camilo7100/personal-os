"""Planes de trabajo: cursos (p. ej. Coursera), proyectos y metas con etapas, ítems y cronograma automático.
Pega el temario (Markdown o texto copiado de la página del curso), indica horas por semana y días de trabajo, y el sistema
reparte los ítems en el calendario, calcula el avance frente a lo previsto y lo muestra en HOY y en un diagrama de Gantt."""
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import typer

from . import db
from .util import JSON, emit

app = typer.Typer(help="Planes de trabajo y cronogramas", no_args_is_help=True)
DAY_NAMES = {"lun": 0, "mar": 1, "mie": 2, "jue": 3, "vie": 4, "sab": 5, "dom": 6}
DAY_LABELS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
STAGE_HEAD = re.compile(r"^\s*(?:#{1,3}\s*|(?:semana|week|m[oó]dulo|module|unidad|unit|secci[oó]n|section|curso|course|fase|etapa|stage|parte|part)\s*\d+\b[:.\-–\s]*)", re.I)
KIND_WORDS = (("quiz", ("quiz", "cuestionario", "examen", "prueba", "graded", "calificad")), ("entrega", ("assignment", "entrega", "proyecto", "peer", "tarea")),
              ("practica", ("lab", "pr[aá]ctica", "ejercicio", "exercise", "hands-on", "notebook")), ("lectura", ("lectura", "reading", "art[ií]culo", "read")),
              ("video", ("video", "v[ií]deo", "lecture", "clase")))
NOISE = re.compile(r"^(show all|hide|ver todo|mostrar|ocultar|completed|completado|resume|reanudar|start|comenzar|\d+%|\d+ of \d+|mark as complete)$", re.I)


# ---------- lectura del temario ----------
def parse_minutes(text: str) -> int | None:
    t = text.lower()
    h = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:h|hr|hrs|hora|horas|hour|hours)\b", t)
    m = re.search(r"(\d+)\s*(?:min|mins|minute|minutes|minutos)\b", t)
    if not (h or m):
        return None
    hours = float(h.group(1).replace(",", ".")) if h else 0.0
    return int(round(hours * 60)) + (int(m.group(1)) if m else 0)


def detect_kind(text: str) -> str:
    t = text.lower()
    for kind, words in KIND_WORDS:
        if any(re.search(w, t) for w in words):
            return kind
    return "leccion"


def parse_outline(text: str, default_minutes: int = 30) -> list[dict]:
    """Devuelve [{title, items:[{title, kind, minutes, url}]}]. Acepta el formato Markdown de PERSONAL_OS
    (`## Etapa` / `- ítem (video, 12 min)`) y el texto pegado de un curso (Semana N / Módulo N + líneas de contenido)."""
    stages: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip().strip("•·▪-*").strip() if not raw.strip().startswith("#") else raw.strip()
        if not line or NOISE.match(line):
            continue
        if re.match(r"^#\s", line) and not stages and not re.match(r"^##", line):
            continue  # título del documento
        if STAGE_HEAD.match(line) and (line.startswith("#") or re.match(r"^\s*(semana|week|m[oó]dulo|module|unidad|unit|fase|etapa|stage|parte|part|secci[oó]n|section)\s*\d+", line, re.I)):
            stages.append({"title": re.sub(r"^#{1,3}\s*", "", line).strip(), "items": []})
            continue
        if not stages:
            stages.append({"title": "Etapa 1", "items": []})
        url = (re.search(r"https?://\S+", line) or [None])[0] if re.search(r"https?://\S+", line) else None
        minutes = parse_minutes(line)
        title = re.sub(r"\((?:[^)]*)\)", lambda m: "" if (parse_minutes(m.group(0)) or detect_kind(m.group(0)) != "leccion") else m.group(0), line)
        title = re.sub(r"https?://\S+", "", title)
        title = re.sub(r"[·•|]\s*\d+\s*(?:min|h|hr)\w*.*$", "", title, flags=re.I).strip(" -–:·")
        if not title:
            continue
        stages[-1]["items"].append({"title": title[:200], "kind": detect_kind(line), "minutes": minutes or default_minutes, "url": url})
    if len(stages) > 1 and stages[0]["title"] == "Etapa 1":
        stages = stages[1:]  # líneas sueltas antes de la primera "Semana/Módulo" (título del curso, etc.)
    return [s for s in stages if s["items"]]


# ---------- cronograma ----------
def parse_days(spec: str) -> list[int]:
    """'lun,mar,jue' o '0,1,3' → [0,1,3]."""
    out = []
    for p in re.split(r"[,\s]+", spec.strip().lower()):
        if p.isdigit() and 0 <= int(p) <= 6:
            out.append(int(p))
        elif p[:3] in DAY_NAMES:
            out.append(DAY_NAMES[p[:3]])
    return sorted(set(out)) or [0, 1, 2, 3, 4]


def schedule(con, plan_id: int, from_date: str | None = None) -> str | None:
    """Reparte los ítems pendientes en los días de trabajo, respetando las horas por semana. Devuelve la fecha final."""
    plan = con.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    days = parse_days(plan["study_days"])
    cap = max(15, int(plan["hours_per_week"] * 60 / len(days)))  # minutos por día de trabajo
    start = date.fromisoformat(from_date or max(plan["start_date"] or date.today().isoformat(), date.today().isoformat()))
    cur, left = start, cap
    items = con.execute("SELECT id, est_minutes FROM plan_items WHERE plan_id=? AND status='todo' ORDER BY stage_id, position", (plan_id,)).fetchall()
    while cur.weekday() not in days:
        cur += timedelta(days=1)
    for it in items:
        if left < it["est_minutes"] and left < cap:  # no cabe en lo que queda del día: siguiente día de trabajo
            cur += timedelta(days=1)
            while cur.weekday() not in days:
                cur += timedelta(days=1)
            left = cap
        con.execute("UPDATE plan_items SET scheduled_date=? WHERE id=?", (cur.isoformat(), it["id"]))
        left -= it["est_minutes"]
    for st in con.execute("SELECT id FROM plan_stages WHERE plan_id=?", (plan_id,)).fetchall():
        r = con.execute("SELECT MIN(scheduled_date), MAX(scheduled_date) FROM plan_items WHERE stage_id=?", (st["id"],)).fetchone()
        con.execute("UPDATE plan_stages SET start_date=?, end_date=? WHERE id=?", (r[0], r[1], st["id"]))
    end = con.execute("SELECT MAX(scheduled_date) FROM plan_items WHERE plan_id=?", (plan_id,)).fetchone()[0]
    con.execute("UPDATE plans SET end_date=?, start_date=COALESCE(start_date, ?) WHERE id=?", (end, start.isoformat(), plan_id))
    db.audit(con, "plan.schedule", "WRITE", f"plan:{plan_id}", detail=f"hasta {end}")
    return end


def create_plan(con, title: str, outline: str, kind: str = "curso", start: str | None = None, hours_per_week: float = 5,
                days: str = "lun,mar,mie,jue,vie", project: str | None = None, entity: str = "personal", tags: str = "",
                source_url: str = "", notes: str = "") -> int:
    stages = parse_outline(outline)
    if not stages:
        raise ValueError("No encontré etapas ni ítems en el temario. Usa '## Etapa' y '- ítem (video, 12 min)'.")
    from . import services
    cur = con.execute(
        "INSERT INTO plans(title, kind, source_url, start_date, hours_per_week, study_days, project_id, entity, tags, notes) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (title.strip(), kind, source_url, start or date.today().isoformat(), hours_per_week, ",".join(map(str, parse_days(days))),
         services.project_id(con, project), entity, services.norm_tags(tags), notes))
    pid = cur.lastrowid
    for si, st in enumerate(stages, 1):
        sid = con.execute("INSERT INTO plan_stages(plan_id, position, title) VALUES (?,?,?)", (pid, si, st["title"])).lastrowid
        for ii, it in enumerate(st["items"], 1):
            con.execute("INSERT INTO plan_items(plan_id, stage_id, position, title, kind, est_minutes, url) VALUES (?,?,?,?,?,?,?)",
                        (pid, sid, ii, it["title"], it["kind"], it["minutes"], it["url"]))
    db.audit(con, "plan.create", "WRITE", f"plan:{pid}", detail=title)
    schedule(con, pid, start)
    return pid


def toggle_item(con, item_id: int, actor: str | None = None) -> bool:
    row = con.execute("SELECT plan_id, status FROM plan_items WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise ValueError("ítem inexistente")
    done = row["status"] != "done"
    con.execute("UPDATE plan_items SET status=?, done_at=? WHERE id=?", ("done" if done else "todo", datetime.now().isoformat(timespec="seconds") if done else None, item_id))
    left = con.execute("SELECT COUNT(*) FROM plan_items WHERE plan_id=? AND status='todo'", (row["plan_id"],)).fetchone()[0]
    con.execute("UPDATE plans SET status=? WHERE id=? AND status!='paused'", ("done" if left == 0 else "active", row["plan_id"]))
    db.audit(con, "plan.item", "WRITE", f"item:{item_id}", detail="hecho" if done else "pendiente", actor=actor)
    return done


def progress(con, plan_id: int, today: str | None = None) -> dict:
    today = today or date.today().isoformat()
    r = con.execute("SELECT COUNT(*) n, SUM(status='done') d, SUM(est_minutes) m, SUM(CASE WHEN status='done' THEN est_minutes ELSE 0 END) dm, "
                    "SUM(CASE WHEN scheduled_date<=? THEN 1 ELSE 0 END) due FROM plan_items WHERE plan_id=? AND status!='skipped'", (today, plan_id)).fetchone()
    n, d, due = r["n"] or 0, r["d"] or 0, r["due"] or 0
    late = con.execute("SELECT COUNT(*) FROM plan_items WHERE plan_id=? AND status='todo' AND scheduled_date<?", (plan_id, today)).fetchone()[0]
    return {"items": n, "done": d, "percent": round(100 * d / n) if n else 0, "minutes": r["m"] or 0, "done_minutes": r["dm"] or 0,
            "late": late, "state": "sin datos" if not n else "al día" if late == 0 else "atrasado" if late > 3 else "casi al día"}


def plan_today(con, today: str | None = None) -> list[dict]:
    """Ítems de hoy y atrasados (no hechos) de los planes activos: alimenta HOY."""
    today = today or date.today().isoformat()
    return [dict(r) for r in con.execute(
        "SELECT i.id, i.title, i.kind, i.est_minutes, i.scheduled_date, p.id AS plan_id, p.title AS plan, s.title AS stage "
        "FROM plan_items i JOIN plans p ON p.id=i.plan_id JOIN plan_stages s ON s.id=i.stage_id "
        "WHERE p.status='active' AND i.status='todo' AND i.scheduled_date<=? ORDER BY i.scheduled_date, p.id, i.stage_id, i.position LIMIT 60", (today,))]


def gantt(rows: list[dict], today: str | None = None) -> dict:
    """rows: [{label, start, end, percent}] → posiciones en % para dibujar el diagrama (CSS, sin JavaScript)."""
    rows = [r for r in rows if r.get("start") and r.get("end")]
    if not rows:
        return {"rows": [], "weeks": [], "today": None}
    lo = date.fromisoformat(min(r["start"] for r in rows))
    lo -= timedelta(days=lo.weekday())
    hi = date.fromisoformat(max(r["end"] for r in rows)) + timedelta(days=7)
    total = max(1, (hi - lo).days)
    pct = lambda d: round(100 * (date.fromisoformat(d) - lo).days / total, 2)
    weeks, w = [], lo
    while w < hi:
        weeks.append({"label": w.strftime("%d/%m"), "left": pct(w.isoformat())})
        w += timedelta(days=7)
    step = max(1, -(-len(weeks) // 12))  # como máximo ~12 etiquetas en el eje
    for i, wk in enumerate(weeks):
        wk["show"] = i % step == 0
    t = today or date.today().isoformat()
    out = [{**r, "left": pct(r["start"]), "width": max(1.2, round(100 * ((date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days + 1) / total, 2))} for r in rows]
    return {"rows": out, "weeks": weeks, "today": pct(t) if lo <= date.fromisoformat(t) <= hi else None}


def to_ics(con, plan_id: int, hour: int = 19) -> str:
    """Archivo de calendario (.ics) con un bloque de trabajo por día; se importa a Google Calendar/Outlook."""
    plan = con.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    days = con.execute("SELECT scheduled_date d, SUM(est_minutes) m, GROUP_CONCAT(title, ' | ') t FROM plan_items WHERE plan_id=? AND status='todo' AND scheduled_date IS NOT NULL "
                       "GROUP BY scheduled_date ORDER BY scheduled_date", (plan_id,)).fetchall()
    esc = lambda s: s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//PERSONAL_OS//ES", "CALSCALE:GREGORIAN"]
    for d in days:
        start = datetime.fromisoformat(d["d"]).replace(hour=hour)
        end = start + timedelta(minutes=max(15, d["m"]))
        L += ["BEGIN:VEVENT", f"UID:plan{plan_id}-{d['d']}@personal-os", f"DTSTAMP:{datetime.utcnow():%Y%m%dT%H%M%SZ}",
              f"DTSTART:{start:%Y%m%dT%H%M%S}", f"DTEND:{end:%Y%m%dT%H%M%S}", f"SUMMARY:{esc('📚 ' + plan['title'])}",
              f"DESCRIPTION:{esc(d['t'][:900])}", "BEGIN:VALARM", "TRIGGER:-PT30M", "ACTION:DISPLAY", "DESCRIPTION:Bloque de estudio", "END:VALARM", "END:VEVENT"]
    return "\r\n".join(L + ["END:VCALENDAR", ""])


def export_ics(con, plan_id: int) -> Path:
    out = db.DATA_DIR / "exports"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"plan-{plan_id}.ics"
    path.write_text(to_ics(con, plan_id), encoding="utf-8")
    return path


# ---------- CLI ----------
@app.command("create")
def create_cmd(title: str, file: Path = typer.Argument(..., help="Archivo con el temario (Markdown o texto pegado)"),
               kind: str = "curso", start: str = typer.Option(None, help="YYYY-MM-DD"), hours_per_week: float = 5,
               days: str = "lun,mar,mie,jue,vie", project: str = typer.Option(None), tags: str = "", url: str = "") -> None:
    """Crea un plan desde un temario y lo programa en el calendario."""
    try:
        with db.session() as con:
            pid = create_plan(con, title, file.read_text(encoding="utf-8"), kind, start, hours_per_week, days, project, "personal", tags, url)
            end = con.execute("SELECT end_date FROM plans WHERE id=?", (pid,)).fetchone()[0]
    except (ValueError, Exception) as e:
        raise typer.BadParameter(str(e))
    typer.echo(f"plan #{pid}: termina el {end}")


@app.command("list")
def list_cmd(as_json: bool = JSON) -> None:
    with db.session() as con:
        rows = [dict(r) | progress(con, r["id"]) for r in con.execute("SELECT id, title, kind, status, start_date, end_date, hours_per_week FROM plans ORDER BY status, end_date")]
    emit(rows, as_json, ["id", "title", "status", "percent", "state", "end_date"])


@app.command("show")
def show_cmd(plan_id: int, as_json: bool = JSON) -> None:
    """Etapas e ítems con fechas (útil para preguntarle a Claude sobre el plan)."""
    with db.session() as con:
        p = dict(con.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone())
        stages = [dict(s) | {"items": [dict(i) for i in con.execute("SELECT id, title, kind, est_minutes, scheduled_date, status FROM plan_items WHERE stage_id=? ORDER BY position", (s["id"],))]}
                  for s in con.execute("SELECT * FROM plan_stages WHERE plan_id=? ORDER BY position", (plan_id,))]
        data = {"plan": p, "progress": progress(con, plan_id), "stages": stages}
    if as_json:
        import json
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return
    typer.echo(f"{p['title']} — {data['progress']['percent']}% · {data['progress']['state']} · hasta {p['end_date']}")
    for s in stages:
        typer.echo(f"\n## {s['title']} ({s['start_date']} → {s['end_date']})")
        for i in s["items"]:
            typer.echo(f"  [{'x' if i['status'] == 'done' else ' '}] #{i['id']} {i['scheduled_date']} {i['title']} ({i['est_minutes']} min)")


@app.command("done")
def done_cmd(item_id: int) -> None:
    with db.session() as con:
        typer.echo("hecho" if toggle_item(con, item_id) else "pendiente")


@app.command("reschedule")
def reschedule_cmd(plan_id: int) -> None:
    """Reprograma lo pendiente desde hoy (si te atrasaste)."""
    with db.session() as con:
        typer.echo(f"termina el {schedule(con, plan_id, date.today().isoformat())}")


@app.command("ics")
def ics_cmd(plan_id: int) -> None:
    with db.session() as con:
        typer.echo(str(export_ics(con, plan_id)))
