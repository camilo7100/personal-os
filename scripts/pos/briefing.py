"""Briefing de la mañana, revisión de la noche y revisión semanal. Todo determinista (sin gastar tokens):
los datos vienen de SQLite y el resultado queda en data/briefings/*.md, listo para leer o para que Claude lo amplíe."""
import json
from datetime import date, timedelta
from pathlib import Path

import typer

from . import agenda, db, life, money, notify, planner, plans

app = typer.Typer(help="Briefing y revisiones", no_args_is_help=True)
OUT = db.DATA_DIR / "briefings"


def latest_health(con) -> list[dict]:
    """Última lectura de cada métrica; se ignoran las que dejaron de medirse (más de 1 día sin actualizarse)."""
    return [dict(r) for r in con.execute(
        "SELECT h.metric, h.value, h.status, h.detail FROM health h JOIN "
        "(SELECT metric, MAX(id) AS mid FROM health GROUP BY metric) m ON m.mid=h.id "
        "WHERE h.ts >= datetime('now','-1 day') ORDER BY h.metric")]


def gather(con, today: str | None = None) -> dict:
    today = today or date.today().isoformat()
    d = date.fromisoformat(today)
    week = (d + timedelta(days=7)).isoformat()
    q = lambda sql, *a: [dict(r) for r in con.execute(sql, a)]
    open_ = "parent_id IS NULL AND someday=0 AND status IN ('todo','doing','blocked')"
    return {
        "date": today,
        "overdue": q(f"SELECT id, priority AS p, due_date, title, tags, recurrence FROM tasks WHERE {open_} AND due_date < ? ORDER BY due_date", today),
        "today": q(f"SELECT id, priority AS p, due_date, title, tags, recurrence FROM tasks WHERE {open_} AND (due_date=? OR scheduled_date=?) ORDER BY priority", today, today),
        "next7": q(f"SELECT id, due_date, title FROM tasks WHERE {open_} AND due_date > ? AND due_date <= ? ORDER BY due_date", today, week),
        "plan_today": plans.plan_today(con, today),
        "blocks": [i for i in agenda.items(con, d, d, {"bloque", "evento"}) if i["time"]],
        "load": planner.load(con, d),
        "questions_pending": con.execute("SELECT COUNT(*) FROM questions WHERE status='pending'").fetchone()[0],
        "done_today": q("SELECT id, title FROM tasks WHERE status='done' AND date(done_at,'localtime')=date(?)", today),
        "done_7d": q("SELECT id, title FROM tasks WHERE status='done' AND date(done_at) > date(?, '-7 day') ORDER BY done_at", today),
        "blocked_projects": q("SELECT name, next_milestone FROM projects WHERE status='blocked'"),
        "inbox_new": con.execute("SELECT COUNT(*) FROM inbox WHERE status='new'").fetchone()[0],
        "emails_for_claude": con.execute("SELECT COUNT(*) FROM emails WHERE needs_claude=1 AND reviewed=0").fetchone()[0],
        "habits": life.habit_status(con, today),
        "upcoming": life.upcoming(con, 14),
        "applications": q("SELECT c.name AS company, a.role, a.status, a.next_step, a.next_step_date FROM applications a "
                          "JOIN companies c ON c.id=a.company_id WHERE a.status IN ('applied','interview','offer') "
                          "AND a.next_step_date IS NOT NULL AND a.next_step_date <= ? ORDER BY a.next_step_date", week),
        "health_warnings": [h for h in latest_health(con) if h["status"] != "ok"],
        "alerts": q("SELECT id, level, title, detail, link FROM alerts WHERE acknowledged=0 ORDER BY CASE level WHEN 'CRITICAL' THEN 0 WHEN 'IMPORTANT' THEN 1 ELSE 2 END, id DESC LIMIT 12"),
        "jobs_failed_24h": q("SELECT j.job, j.message FROM job_runs j JOIN (SELECT job, MAX(id) AS mid FROM job_runs GROUP BY job) m ON m.mid=j.id "
                            "WHERE j.status='failed' AND j.started_at >= datetime('now','-1 day')"),  # solo si su ÚLTIMA corrida falló
        "repos_dirty": q("SELECT name, dirty FROM repos WHERE dirty>0 ORDER BY dirty DESC"),
        "money": money.month_summary(con, today[:7]),
        "subscriptions_monthly": life.monthly_cost(con),
    }


def _list(title: str, items: list[str]) -> list[str]:
    return [f"### {title}", *(items or ["- (nada)"]), ""]


def render(kind: str, g: dict) -> str:
    t = lambda r: f"- #{r['id']} {'⚠ ' if r.get('p') == 1 else ''}{r['title']}" + (f" _(vence {r['due_date']})_" if r.get("due_date") else "")
    L = [f"# {'Briefing de la mañana' if kind == 'morning' else 'Revisión de la noche' if kind == 'evening' else 'Revisión semanal'} — {g['date']}", ""]
    if kind == "morning":
        L += _list("Vencidas", [t(r) for r in g["overdue"]]) + _list("Para hoy", [t(r) for r in g["today"]])
        L += _list("Próximos 7 días", [t(r) for r in g["next7"]])
    elif kind == "evening":
        L += _list("Completado hoy", [t(r) for r in g["done_today"]])
        L += _list("Pendiente (reprograma o descarta)", [t(r) for r in g["overdue"] + g["today"]])
        L += _list("Mañana y próximos días", [t(r) for r in g["next7"][:8]])
    else:
        L += _list("Completado en 7 días", [t(r) for r in g["done_7d"]]) + _list("Sigue abierto y vencido", [t(r) for r in g["overdue"]])
        L += _list("Proyectos bloqueados", [f"- {p['name']} — {p['next_milestone'] or ''}" for p in g["blocked_projects"]])
        m = g["money"]
        L += _list(f"Finanzas {m['month']}", [f"- ingresos {m['income']:,.0f} · gastos {m['expense']:,.0f} · neto {m['net']:,.0f}",
                                             f"- suscripciones: {g['subscriptions_monthly']:,.0f}/mes"])
    if g["plan_today"]:
        L += _list("Planes de trabajo (hoy y atrasado)", [f"- {'⚠ ' if i['scheduled_date'] < g['date'] else ''}**{i['plan']}** · {i['title']} ({i['est_minutes']} min)" for i in g["plan_today"][:12]])
    L += _list("Hábitos", [f"- {'✅' if h['done_today'] else '⬜'} {h['name']} (racha {h['streak']})" for h in g["habits"]])
    u = g["upcoming"]
    L += _list("Vencimientos y fechas (14 días)",
               [f"- documento **{x['name']}** vence {x['expires_on']}" for x in u["documents"]]
               + [f"- suscripción **{x['name']}** {x['amount']:,.0f} el {x['next_date']}" for x in u["subscriptions"]]
               + [f"- cumpleaños de **{x['name']}** el {x['date']}" for x in u["birthdays"]])
    fam = [f"- {e['title']} — {e['date']} (en {e['left']} d)" for e in u.get("family", []) if e["left"] <= 45]
    if fam:
        L += _list("Familia y pareja (próximos 45 días)", fam)
    if u.get("holidays"):
        L += _list("Festivos próximos", [f"- {h['name']} — {h['date']}" for h in u["holidays"]])
    if g["applications"]:
        L += _list("Empleo", [f"- {a['company']} ({a['role']}): {a['next_step'] or a['status']} — {a['next_step_date']}" for a in g["applications"]])
    L += _list("Bandeja", [f"- Inbox sin clasificar: **{g['inbox_new']}**", f"- Correos que necesitan a Claude: **{g['emails_for_claude']}**", f"- Preguntas pendientes para Claude: **{g['questions_pending']}**"])
    risk = [f"- ⚠ {h['metric']}: {h['detail'] or h['value']}" for h in g["health_warnings"]] \
        + [f"- ✖ job fallido {j['job']}: {j['message']}" for j in g["jobs_failed_24h"]] \
        + [f"- 🛠 repo {r['name']} con {r['dirty']} cambios sin guardar" for r in g["repos_dirty"]] \
        + [f"- 🔔 {a['level']}: {a['title']}" for a in g["alerts"]]
    L += _list("Riesgos y sistema", risk)
    return "\n".join(L)


def write(kind: str) -> str:
    with db.session() as con:
        g = gather(con)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{g['date']}-{kind}.md"
    path.write_text(render(kind, g), encoding="utf-8")
    headline = f"{len(g['overdue'])} vencidas · {len(g['today'])} hoy · {g['inbox_new']} en inbox · {g['emails_for_claude']} correos"
    if kind == "morning":  # el resumen no es una alerta: aviso discreto de Windows y (abajo) mensaje a Telegram
        notify.toast("Tu día — PERSONAL_OS", headline)
    from . import tgfmt
    notify.push_text({"morning": tgfmt.morning, "evening": tgfmt.evening}.get(kind, tgfmt.weekly)(g))  # corto y accionable; el detalle está en el portal
    with db.session() as con:
        db.audit(con, f"briefing.{kind}", "WRITE", str(path), actor="job")
    return f"{path.name}: {headline}"


@app.command("show")
def show(kind: str = typer.Argument("morning", help="morning | evening | weekly")) -> None:
    """Genera y muestra el briefing (sin notificar)."""
    with db.session() as con:
        typer.echo(render(kind, gather(con)))


@app.command("json")
def as_json() -> None:
    with db.session() as con:
        typer.echo(json.dumps(gather(con), ensure_ascii=False, indent=2, default=str))
