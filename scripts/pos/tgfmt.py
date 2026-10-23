"""Mensajes de Telegram: cortos, concretos y accionables (lista de pendientes con casillas y botones), sin secciones vacías.
Todo es determinista (sin IA). El resumen largo vive en el portal y en data\\briefings\\."""
from datetime import date, datetime

DAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
MAX_ITEMS = 6
LEVEL = {"CRITICAL": "🔴 Urgente", "IMPORTANT": "🟠 Importante", "INFO": "🔵 Aviso"}


def short(d: str | date | None) -> str:
    """'2026-10-02' → 'vie 2 oct'."""
    if not d:
        return ""
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def rel(d: str, today: date) -> str:
    n = (date.fromisoformat(d) - today).days
    return "hoy" if n == 0 else "mañana" if n == 1 else f"en {n} d" if n > 0 else f"hace {-n} d"


def _items(rows: list[dict], today: date, with_due: bool = True) -> list[str]:
    out = [f"☐ {r['id']} · {r['title'][:70]}" + (f" ({rel(r['due_date'], today)})" if with_due and r.get("due_date") and r["due_date"] != today.isoformat() else "")
           for r in rows[:MAX_ITEMS]]
    if len(rows) > MAX_ITEMS:
        out.append(f"… y {len(rows) - MAX_ITEMS} más (/tareas)")
    return out


def _next_action(g: dict) -> dict | None:
    """La mejor siguiente acción: lo vencido más urgente; si no, lo de hoy de mayor prioridad."""
    pool = sorted(g["overdue"], key=lambda r: (r.get("p") or 3, r["due_date"] or "")) or sorted(g["today"], key=lambda r: r.get("p") or 3)
    return pool[0] if pool else None


def morning(g: dict) -> str:
    today = date.fromisoformat(g["date"])
    L = [f"☀️ Buen día · {short(today)}"]
    nxt = _next_action(g)
    if nxt:
        L += ["", f"👉 Empieza por: {nxt['title'][:80]} (#{nxt['id']})"]
    if g["overdue"]:
        L += ["", f"⚠️ Vencidas ({len(g['overdue'])})", *_items(g["overdue"], today)]
    if g["today"]:
        L += ["", f"📌 Hoy ({len(g['today'])})", *_items(g["today"], today, False)]
    blocks = g.get("blocks") or []
    if blocks:
        L += ["", "🕘 Tu día", *[f"{b['time']} · {b['title'][:56]}" for b in blocks[:6]]]
    load = g.get("load") or {}
    if load.get("state") == "over":
        L += ["", f"⚠️ Hoy es demasiado: {load['count']} tareas, {load['minutes'] // 60} h {load['minutes'] % 60} min (capacidad {load['capacity'] // 60} h). Mueve lo menos importante."]
    plan = g.get("plan_today") or []
    if plan:
        L += ["", "📚 Plan de hoy", *[f"• {i['title'][:60]} ({i['est_minutes']} min)" for i in plan[:4]]]
    soon = [r for r in g["next7"] if r["due_date"] > today.isoformat()][:4]
    if soon:
        L += ["", "🗓 Esta semana", *[f"• {short(r['due_date'])}: {r['title'][:60]}" for r in soon]]
    up = g["upcoming"]
    dates = [f"🎁 {e['title'][:48]} — {rel(e['date'], today)}" for e in up.get("family", []) if e["left"] <= 30][:3]
    dates += [f"📄 {d['name'][:40]} vence {short(d['expires_on'])}" for d in up["documents"][:2]]
    dates += [f"🔁 {s['name'][:30]} se cobra {short(s['next_date'])}" for s in up["subscriptions"][:2]]
    dates += [f"🇨🇴 {h['name']} — {short(h['date'])}" for h in up.get("holidays", [])[:1]]
    if dates:
        L += ["", *dates]
    habits = [h for h in g["habits"] if not h["done_today"]]
    if habits:
        L += ["", "💪 Hábitos de hoy: " + " · ".join(f"{h['name'][:24]}" + (f" 🔥{h['streak']}" if h["streak"] >= 3 else "") for h in habits[:4])]
    if not (g["overdue"] or g["today"] or plan):
        L += ["", "✨ Día despejado. Nada vence hoy."]
    foot = []
    if g["alerts"]:
        foot.append(f"🔔 {len(g['alerts'])} alerta(s): /alertas")
    if g["inbox_new"]:
        foot.append(f"📥 {g['inbox_new']} por clasificar")
    if foot:
        L += ["", " · ".join(foot)]
    L += ["", "✅ /hecho N · ⏰ /pospon N mañana · ➕ /tarea texto"]
    return "\n".join(L)


def evening(g: dict) -> str:
    today = date.fromisoformat(g["date"])
    done, pend = g["done_today"], g["overdue"] + g["today"]
    L = [f"🌙 Cierre del día · {short(today)}", "", f"✔ Hechas hoy: {len(done)}" + (" — " + "; ".join(d["title"][:32] for d in done[:3]) if done else "")]
    if pend:
        L += ["", f"Quedó pendiente ({len(pend)})", *_items(pend, today), "", "¿Pasa a mañana? /pospon N mañana · o /hecho N"]
    else:
        L += ["", "🎉 Sin pendientes. Descansa."]
    tom = [r for r in g["next7"] if r["due_date"] == (date.fromordinal(today.toordinal() + 1)).isoformat()]
    if tom:
        L += ["", "Mañana vence:", *[f"• {r['title'][:60]}" for r in tom[:4]]]
    return "\n".join(L)


def weekly(g: dict) -> str:
    today = date.fromisoformat(g["date"])
    L = [f"📊 Tu semana · {short(today)}", "", f"✔ Hechas en 7 días: {len(g['done_7d'])}", f"⚠️ Vencidas abiertas: {len(g['overdue'])}"]
    m = g.get("money") or {}
    if m.get("expense") or m.get("income"):
        L += [f"💰 {m['month']}: gastos {m['expense']:,.0f} · ingresos {m['income']:,.0f} · neto {m['net']:,.0f}"]
    if g["overdue"]:
        L += ["", "Para ponerte al día:", *_items(g["overdue"], today)]
    if g["next7"]:
        L += ["", "Próximos 7 días:", *[f"• {short(r['due_date'])}: {r['title'][:56]}" for r in g["next7"][:6]]]
    if g["blocked_projects"]:
        L += ["", "⛔ Bloqueados: " + ", ".join(p["name"] for p in g["blocked_projects"][:3])]
    return "\n".join(L)


def alert(level: str, title: str, detail: str = "") -> str:
    d = (detail or "").strip().splitlines()
    return "\n".join([f"{LEVEL.get(level, '🔔 Aviso')} · {title[:120]}", *([d[0][:160]] if d else [])])


def buttons(key: str | None, alert_id: int | None) -> list[list[tuple[str, str]]]:
    """Botones en línea según el tipo de alerta (el texto del botón, lo que se envía de vuelta)."""
    rows: list[list[tuple[str, str]]] = []
    k = (key or "").split(":")
    if k[0] in ("task-overdue", "task-soon") and len(k) > 1 and k[1].isdigit():
        rows.append([("✅ Hecho", f"done:{k[1]}"), ("⏰ Mañana", f"snooze:{k[1]}")])
    if k[0] == "coursera-proposal":
        rows.append([("✅ Aplicar cambios", "coursera:si"), ("🚫 Descartar", "coursera:no")])
    if alert_id:
        rows.append([("👌 Entendido", f"ok:{alert_id}")])
    return rows


HELP = ("📋 Comandos\n"
        "/hoy · tu día  |  /dia · tus bloques de hoy  |  /semana · 7 días\n"
        "/tareas · pendientes  |  /alertas · avisos\n"
        "/tarea texto viernes · crear\n"
        "/hecho N · /pospon N mañana\n"
        "/gastos · finanzas del mes  |  /plan · /buscar texto · /pregunta texto\n"
        "/coursera si|no  |  /dispositivo · añadir celular o tableta\n"
        "Cualquier otro mensaje (texto o voz) va a tu inbox.")


def week(g_rows: list[dict], today: date) -> str:
    """Agenda de 7 días: lo que vence cada día."""
    if not g_rows:
        return "🗓 Semana despejada: nada vence en 7 días."
    L = ["🗓 Próximos 7 días"]
    cur = None
    for r in g_rows:
        if r["due_date"] != cur:
            cur = r["due_date"]
            L += ["", f"{short(cur)} · {rel(cur, today)}"]
        L.append(f"☐ {r['id']} · {r['title'][:64]}")
    return "\n".join(L)


def now_str() -> str:
    return datetime.now().strftime("%H:%M")
