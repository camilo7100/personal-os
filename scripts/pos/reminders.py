"""Motor de alertas: convierte TUS DATOS (fechas límite, vencimientos, cumpleaños, planes, empleo, salud, correo) en alertas.
Es determinista y barato: corre cada hora sobre SQLite, crea las alertas nuevas (sin repetir), archiva las que ya no aplican y
envía al PC/celular las importantes respetando las horas de silencio. Umbrales editables en config\\alerts.json."""
import json
import re
from datetime import date, datetime, timedelta

import typer

from . import dates, db, notify, plans
from .util import JSON, emit

app = typer.Typer(help="Alertas y recordatorios", no_args_is_help=True)
CONFIG = db.ROOT / "config" / "alerts.json"
DEFAULTS = {
    "doc_days": [60, 30, 7],          # avisos antes del vencimiento (7 días o menos = crítico; vencido = crítico)
    "sub_days": 3,                    # renovación de suscripciones
    "birthday_days": [7, 0],          # otras personas: 7 días antes (informativo) y el mismo día (importante)
    "close_days": [30, 15, 0],        # familia y pareja (cumpleaños, aniversario, fechas especiales): 1 mes, 15 días y el día
    "application_days": 1,            # siguiente paso de una postulación
    "habit_evening_hour": 20,         # después de esta hora avisa si una racha ≥ 3 está en riesgo
    "mail_backlog": 10,
    "inbox_backlog": 10,
    "max_overdue_individual": 5,      # con más tareas vencidas se agrupan en una sola alerta
}


def cfg() -> dict:
    try:
        return {**DEFAULTS, **json.loads(CONFIG.read_text(encoding="utf-8"))}
    except Exception:
        return dict(DEFAULTS)


Rule = tuple[str, str, str, str, str]  # (key, level, title, detail, link)


def evaluate(con, now: datetime | None = None) -> list[Rule]:
    """Todas las alertas que HOY aplican según los datos."""
    now = now or datetime.now()
    today = now.date()
    c = cfg()
    out: list[Rule] = []
    iso = today.isoformat()

    # 1) tareas vencidas
    overdue = con.execute("SELECT id, title, due_date, priority FROM tasks WHERE status IN ('todo','doing','blocked') AND due_date < ? ORDER BY due_date", (iso,)).fetchall()
    if len(overdue) > c["max_overdue_individual"]:
        out.append((f"tasks-overdue:{iso}", "IMPORTANT", f"{len(overdue)} tareas vencidas", "Reprograma o descarta las que ya no importan.", "/tasks"))
    else:
        for t in overdue:
            days = (today - date.fromisoformat(t["due_date"])).days
            out.append((f"task-overdue:{t['id']}", "CRITICAL" if t["priority"] == 1 else "IMPORTANT", f"Vencida hace {days} d: {t['title']}"[:140], f"Fecha límite {t['due_date']} · responde /hecho {t['id']} o /pospon {t['id']} mañana", "/tasks"))

    # 1b) aviso el día antes (y el mismo día) de las tareas importantes y de los pagos: para no enterarte cuando ya venció
    tomorrow = (today + timedelta(days=1)).isoformat()
    for t in con.execute("SELECT id, title, due_date, priority, tags FROM tasks WHERE status IN ('todo','doing','blocked') AND due_date IN (?, ?) "
                         "AND (priority <= 2 OR tags LIKE '%pago%')", (iso, tomorrow)):
        when = "HOY" if t["due_date"] == iso else "mañana"
        out.append((f"task-soon:{t['id']}:{t['due_date']}", "IMPORTANT", f"Vence {when}: {t['title']}"[:140], f"Responde /hecho {t['id']} cuando lo hagas o /pospon {t['id']} viernes", "/tasks"))

    # 1c) bloques de tiempo que empiezan en menos de 15 minutos (aviso accionable al PC y al celular)
    nowm = now.hour * 60 + now.minute
    for t in con.execute("SELECT id, title, start_time, est_minutes FROM tasks WHERE status IN ('todo','doing','blocked') AND scheduled_date=? AND start_time IS NOT NULL", (iso,)):
        h, mi = map(int, t["start_time"].split(":"))
        left = h * 60 + mi - nowm
        if 0 <= left <= 15:
            out.append((f"block:{t['id']}:{iso}:{t['start_time']}", "IMPORTANT", f"⏰ {t['start_time']} · {t['title']}"[:140], f"Empieza en {left} min" + (f" · {t['est_minutes']} min" if t["est_minutes"] else ""), f"/tasks/{t['id']}"))

    # 2) documentos por vencer
    for d in con.execute("SELECT id, name, expires_on FROM documents WHERE expires_on IS NOT NULL"):
        left = (date.fromisoformat(d["expires_on"]) - today).days
        if left < 0:
            out.append((f"doc:{d['id']}:expired", "CRITICAL", f"VENCIDO: {d['name']}", f"Venció el {d['expires_on']}", "/life"))
            continue
        for limit in sorted(c["doc_days"]):
            if left <= limit:
                level = "CRITICAL" if limit <= 7 else "IMPORTANT" if limit <= 30 else "INFO"
                out.append((f"doc:{d['id']}:{limit}", level, f"{d['name']} vence en {left} d", f"Vence el {d['expires_on']}", "/life"))
                break

    # 3) suscripciones que se renuevan
    for s in con.execute("SELECT id, name, amount, next_date FROM subscriptions WHERE active=1 AND next_date IS NOT NULL"):
        left = (date.fromisoformat(s["next_date"]) - today).days
        if 0 <= left <= c["sub_days"]:
            out.append((f"sub:{s['id']}:{s['next_date']}", "IMPORTANT", f"Se cobra {s['name']} en {left} d", f"{s['amount']:,.0f} el {s['next_date']}", "/life"))

    # 4) cumpleaños de otras personas
    for p in con.execute("SELECT id, name, birthday FROM people WHERE birthday IS NOT NULL AND COALESCE(circle,'otros')='otros'"):
        try:
            nxt = dates.next_occurrence(p["birthday"], today)
        except ValueError:
            continue
        left = (nxt - today).days
        if left == 0:
            out.append((f"bday:{p['id']}:{nxt.year}:0", "IMPORTANT", f"Hoy cumple años {p['name']}", "Escríbele o llámale.", "/life"))
        elif left in c["birthday_days"]:
            out.append((f"bday:{p['id']}:{nxt.year}:{left}", "INFO", f"{p['name']} cumple años en {left} d", nxt.isoformat(), "/life"))

    # 4b) familia y pareja: todo evento ligado a ellos avisa 1 mes y 15 días antes (y el mismo día)
    for ev in dates.circle_events(con, today, max(c["close_days"])):
        if ev["left"] not in c["close_days"]:
            continue
        d = date.fromisoformat(ev["date"])
        key = f"close:{ev['kind'][:3]}:{ev.get('id') or ev['who']}:{d.year}:{ev['left']}"
        if ev["left"] == 0:
            out.append((key, "IMPORTANT", f"Hoy: {ev['title']}", "Escríbele o llámale.", "/life"))
        else:
            out.append((key, "IMPORTANT", f"{ev['title']} en {ev['left']} días ({d:%d/%m})", "Momento de pensar el regalo o el plan." if ev["left"] >= 15 else "", "/life"))

    # 5) planes atrasados
    for pl in con.execute("SELECT id, title FROM plans WHERE status='active'"):
        pr = plans.progress(con, pl["id"], iso)
        if pr["late"] > 3:
            out.append((f"plan-late:{pl['id']}:{iso}", "IMPORTANT", f"Plan atrasado: {pl['title']}", f"{pr['late']} ítems atrasados. Reprograma desde el plan.", f"/plans/{pl['id']}"))
        elif pr["late"] > 0:
            out.append((f"plan-slip:{pl['id']}:{iso}", "INFO", f"{pl['title']}: {pr['late']} ítem(s) atrasado(s)", "", f"/plans/{pl['id']}"))

    # 6) empleo: siguiente paso próximo o vencido
    lim = (today + timedelta(days=c["application_days"])).isoformat()
    for a in con.execute("SELECT a.id, a.role, a.next_step, a.next_step_date, co.name AS company FROM applications a JOIN companies co ON co.id=a.company_id "
                         "WHERE a.status IN ('saved','applied','interview','offer') AND a.next_step_date IS NOT NULL AND a.next_step_date <= ?", (lim,)):
        out.append((f"app:{a['id']}:{a['next_step_date']}", "IMPORTANT", f"{a['company']}: {a['next_step'] or 'siguiente paso'}", f"{a['role']} · {a['next_step_date']}", "/career"))

    # 7) salud del sistema (solo lo crítico es urgente; los avisos quedan informativos)
    for h in con.execute("SELECT h.metric, h.value, h.status, h.detail FROM health h JOIN (SELECT metric, MAX(id) mid FROM health GROUP BY metric) m ON m.mid=h.id "
                         "WHERE h.ts >= datetime('now','-1 day') AND h.status!='ok'"):
        out.append((f"health:{h['metric']}:{h['status']}", "CRITICAL" if h["status"] == "crit" else "INFO", f"Sistema: {h['metric']} ({h['status']})", h["detail"] or str(h["value"]), "/system"))

    # 8) correo y captura
    recent = "substr(date,1,10) >= date(?, '-3 day')"
    grave = re.compile(r"contrase[ñn]a (fue )?(cambiad|restablecid)|password (was )?(changed|reset)|actividad sospechosa|suspicious|cuenta (bloquead|suspendid|comprometid)|account (locked|suspended|compromised)|no fuiste t[uú]", re.I)
    routine = 0
    for e in con.execute(f"SELECT id, subject FROM emails WHERE category='security' AND needs_claude=1 AND reviewed=0 AND {recent}", (iso,)):
        if grave.search(e["subject"]):
            out.append((f"mail-security:{e['id']}", "CRITICAL", f"Alerta grave en tu correo: {e['subject'][:80]}", "Revísala ahora.", "/mail"))
        else:
            routine += 1  # avisos de «nuevo inicio de sesión»: una sola alerta, no una por correo
    if routine:
        out.append((f"mail-security-routine:{iso}", "IMPORTANT", f"{routine} aviso(s) de seguridad de tus cuentas de correo", "Revisa si reconoces esos accesos y márcalos como revisados.", "/mail"))
    n = con.execute("SELECT COUNT(*) FROM emails WHERE needs_claude=1 AND reviewed=0").fetchone()[0]
    if n >= c["mail_backlog"]:
        out.append((f"mail-backlog:{iso}", "INFO", f"{n} correos esperan tu revisión", "", "/mail"))
    n = con.execute("SELECT COUNT(*) FROM inbox WHERE status='new'").fetchone()[0]
    if n >= c["inbox_backlog"]:
        out.append((f"inbox-backlog:{iso}", "INFO", f"{n} ítems sin clasificar en el inbox", "", "/inbox"))
    n = con.execute("SELECT COUNT(*) FROM questions WHERE status='pending'").fetchone()[0]
    if n:
        out.append(("questions-pending", "INFO", f"{n} pregunta(s) esperan a Claude", "Abre una sesión: «responde mis preguntas».", "/ask"))

    # 9) rachas de hábitos en riesgo (solo de noche)
    if now.hour >= c["habit_evening_hour"]:
        from . import life
        for h in life.habit_status(con, iso):
            if not h["done_today"] and h["streak"] >= 3:
                out.append((f"habit:{h['id']}:{iso}", "IMPORTANT", f"Tu racha de «{h['name']}» ({h['streak']} días) está en riesgo", "Aún estás a tiempo hoy.", "/life"))
    # 10) recordatorio insistente: las tareas con la etiqueta «insistir» avisan todos los días hasta que se hagan
    for t in con.execute("SELECT id, title, due_date FROM tasks WHERE status IN ('todo','doing','blocked') AND tags LIKE '%insistir%'"):
        out.append((f"nag:{t['id']}:{iso}", "IMPORTANT", f"Pendiente: {t['title']}"[:140],
                    f"{'Vence ' + t['due_date'] + ' · ' if t['due_date'] else ''}responde /hecho {t['id']} cuando lo termines o /pospon {t['id']}", "/tasks"))
    return out


def run(now: datetime | None = None) -> str:
    from . import autotasks
    with db.session() as con:
        autotasks.CREATED.clear()
        autotasks.run(con, (now or datetime.now()).date())  # regalos y renovaciones: tareas que nacen solas
        wanted = evaluate(con, now)
        born = list(autotasks.CREATED)
    new = 0
    for level, title, detail in born:  # lo que nació hoy se avisa una sola vez
        new += notify.alert(level, title, detail, key=f"auto:{title[:80]}", source="auto", now=now)
    for key, level, title, detail, link in wanted:
        new += notify.alert(level, title, detail, key=key, source="rule", link=link, now=now)
    keys = {w[0] for w in wanted}
    with db.session() as con:  # archiva lo que ya no aplica (tarea hecha, documento renovado…)
        stale = [r["id"] for r in con.execute("SELECT id, key FROM alerts WHERE source='rule' AND acknowledged=0") if r["key"] not in keys]
        con.executemany("UPDATE alerts SET acknowledged=1 WHERE id=?", [(i,) for i in stale])
    pushed = notify.flush_pending(now)
    return f"{len(wanted)} vigentes, {new} nuevas, {len(stale)} archivadas, {pushed} enviadas"


@app.command("run")
def run_cmd() -> None:
    """Evalúa las reglas ahora."""
    typer.echo(run())


@app.command("list")
def list_cmd(all: bool = False, as_json: bool = JSON) -> None:
    with db.session() as con:
        emit(con.execute("SELECT id, level, ts, source, title FROM alerts" + ("" if all else " WHERE acknowledged=0") + " ORDER BY id DESC LIMIT 60").fetchall(), as_json)


@app.command("ack")
def ack_cmd(alert_id: int = typer.Argument(None, help="Sin número: archiva todas")) -> None:
    with db.session() as con:
        if alert_id:
            con.execute("UPDATE alerts SET acknowledged=1 WHERE id=?", (alert_id,))
        else:
            con.execute("UPDATE alerts SET acknowledged=1")
        db.audit(con, "alerts.ack", "WRITE", str(alert_id or "all"))
    typer.echo("ok")
