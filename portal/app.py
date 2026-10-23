"""Portal de PERSONAL_OS: FastAPI + Jinja2, solo en 127.0.0.1. Lee y escribe por los mismos servicios que la CLI."""
import html
import re
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import markdown  # noqa: E402
from fastapi import FastAPI, File, Form, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402

from pos import agenda as agenda_mod, ai, ask, finance, review as review_mod, statements as stm, briefing, career, db, jobs, life, mail, media, money, notes, notify, plans, planner, quick, services, telegram, triage  # noqa: E402
import views  # noqa: E402
from pos import setup as onboarding  # noqa: E402
from pos import portal_security as sec  # noqa: E402

HERE = Path(__file__).parent
app = FastAPI(title="PERSONAL_OS", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
T = Jinja2Templates(directory=HERE / "templates")
LOCAL = ("127.0.0.1", "localhost")
ACTOR = "portal"

NAV = [  # (grupo, clave, etiqueta, ruta)
    ("Principal", "hoy", "Hoy", "/"), ("Principal", "inbox", "Inbox", "/inbox"), ("Principal", "tareas", "Tareas", "/tasks"), ("Principal", "calendario", "Calendario", "/calendar"), ("Principal", "revision", "Revisión semanal", "/review"),
    ("Principal", "proyectos", "Proyectos", "/projects"), ("Principal", "planes", "Planes", "/plans"), ("Principal", "objetivos", "Objetivos", "/goals"),
    ("Principal", "preguntar", "Preguntar a la IA", "/ask"),
    ("Vida y trabajo", "correo", "Correo", "/mail"), ("Vida y trabajo", "estudio", "Estudio", "/study"),
    ("Vida y trabajo", "carrera", "Carrera", "/career"), ("Vida y trabajo", "finanzas", "Finanzas", "/money"),
    ("Vida y trabajo", "vida", "Vida y documentos", "/life"), ("Vida y trabajo", "knowledge", "Conocimiento", "/knowledge"),
    ("Herramientas", "dispositivos", "Dispositivos", "/devices"), ("Herramientas", "start", "Primeros pasos", "/start"), ("Herramientas", "manual", "Manual", "/manual"), ("Herramientas", "multimedia", "Multimedia", "/media"), ("Herramientas", "desarrollo", "Desarrollo", "/dev"),
    ("Herramientas", "automatizaciones", "Automatizaciones", "/jobs"), ("Herramientas", "sistema", "Sistema", "/system"),
]


CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "frame-ancestors 'none'; form-action 'self'; base-uri 'none'")


def _safe_next(n: str | None) -> str:
    return n if n and n.startswith("/") and not n.startswith("//") and not n.startswith("/login") else "/"


@app.middleware("http")
async def guard(request: Request, call_next):
    """Puerta única: host permitido, origen del formulario, alcance remoto, PIN, inactividad y desbloqueo de lo sensible."""
    host, path = request.headers.get("host", ""), request.url.path
    if not sec.host_allowed(host):  # anti DNS-rebinding: solo este equipo o los nombres que autorizaste
        return PlainTextResponse("forbidden", status_code=403)
    local = sec.is_local_host(host)
    if request.method == "POST":
        origin, site = request.headers.get("origin"), request.headers.get("sec-fetch-site")
        same_site = site in ("same-origin", "none")
        if origin == "null":  # el navegador envía «null» en formularios propios con ciertas políticas de referrer: solo vale si él mismo atestigua que es del mismo origen
            forbidden = not same_site
        else:
            forbidden = bool((origin and urlparse(origin).netloc.lower() != host.lower()) or (site and not same_site))
        if forbidden:
            return PlainTextResponse("forbidden", status_code=403)
    if not local and not sec.remote_permits(path, request.method):
        return PlainTextResponse("Esta sección solo se abre desde tu PC.", status_code=403)
    pin_set, session = sec.pin_is_set(), None
    if not (path.startswith("/static/") or path.startswith("/pair/") or path in ("/login", "/setup", "/sw.js")):
        if not pin_set and not local:
            return PlainTextResponse("Crea primero el PIN en tu PC.", status_code=403)
        if pin_set:
            session = sec.verify(request.cookies.get("pos_session"))
            if not sec.session_valid(session, local=local) or not sec.device_ok(session.get("dev")):
                return RedirectResponse(f"/login?next={quote(path)}", status_code=303)
            if sec.path_matches(path, sec.config()["sensitive"]) and not sec.unlocked(session):
                return RedirectResponse(f"/unlock?next={quote(path)}", status_code=303)
    response = await call_next(request)
    if session and path not in ("/unlock", "/logout"):  # la sesión se renueva con la actividad (esas rutas ya manejan su cookie)
        session["last"] = time.time()
        response.set_cookie("pos_session", sec.sign(session), httponly=True, samesite="strict", secure=not local, max_age=sec.session_seconds(local))
    response.headers.update({"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                             "Referrer-Policy": "same-origin", "Cache-Control": "no-store"})
    return response


def _client_key(request: Request) -> str:
    return f"{request.client.host if request.client else '?'}|{request.headers.get('x-forwarded-for', '')}|{request.headers.get('host', '')}"


def _login_result(request: Request, pin: str, nxt: str, unlock_only: bool):
    key = _client_key(request)
    wait = sec.locked_seconds(key)
    if wait:
        return T.TemplateResponse(request, "login.html", {"error": f"Demasiados intentos. Espera {wait // 60 + 1} min.", "next": nxt, "mode": "unlock" if unlock_only else "login"}, status_code=429)
    if not sec.check_pin(pin):
        n = sec.register_fail(key)
        with db.session() as con:
            db.audit(con, "portal.login", "READ", "portal", "failed", f"intento {n}", actor=ACTOR)
        if n == 5:
            notify.alert("IMPORTANT", "Portal: 5 intentos fallidos de PIN", "Alguien probó el PIN del portal. Se bloqueó 5 minutos.")
        return T.TemplateResponse(request, "login.html", {"error": "PIN incorrecto.", "next": nxt, "mode": "unlock" if unlock_only else "login"}, status_code=401)
    sec.clear_fails(key)
    with db.session() as con:
        db.audit(con, "portal.unlock" if unlock_only else "portal.login", "READ", "portal", actor=ACTOR)
    s = sec.verify(request.cookies.get("pos_session")) if unlock_only else None
    s = s if (s and sec.session_valid(s)) else sec.new_session()
    s["last"] = s["unlock"] = time.time()
    resp = RedirectResponse(_safe_next(nxt), status_code=303)
    resp.set_cookie("pos_session", sec.sign(s), httponly=True, samesite="strict", secure=not sec.is_local_host(request.headers.get("host", "")), max_age=sec.session_seconds(sec.is_local_host(request.headers.get("host", ""))))
    return resp


@app.get("/login")
def login_page(request: Request, next: str = "/"):
    return T.TemplateResponse(request, "login.html", {"error": None, "next": next, "mode": "login"})


@app.post("/login")
def login_post(request: Request, pin: str = Form(...), next: str = Form("/")):
    return _login_result(request, pin, next, False)


@app.get("/unlock")
def unlock_page(request: Request, next: str = "/"):
    return T.TemplateResponse(request, "login.html", {"error": None, "next": next, "mode": "unlock"})


@app.post("/unlock")
def unlock_post(request: Request, pin: str = Form(...), next: str = Form("/")):
    return _login_result(request, pin, next, True)


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("pos_session")
    return resp


@app.get("/setup")
def setup_page(request: Request):
    if sec.pin_is_set() or not sec.is_local_host(request.headers.get("host", "")):
        return RedirectResponse("/", status_code=303)
    return T.TemplateResponse(request, "login.html", {"error": None, "next": "/", "mode": "setup"})


@app.post("/setup")
def setup_post(request: Request, pin: str = Form(...), pin2: str = Form(...)):
    if sec.pin_is_set() or not sec.is_local_host(request.headers.get("host", "")):
        return RedirectResponse("/", status_code=303)
    if pin != pin2 or len(pin) < 6:
        return T.TemplateResponse(request, "login.html", {"error": "Los PIN no coinciden o tienen menos de 6 caracteres.", "next": "/", "mode": "setup"}, status_code=400)
    sec.set_pin(pin)
    with db.session() as con:
        db.audit(con, "portal.pin.set", "SENSITIVE_ACTION", "portal", actor=ACTOR)
    s = sec.new_session()
    s["unlock"] = time.time()
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("pos_session", sec.sign(s), httponly=True, samesite="strict", max_age=60 * 60 * 12)
    return resp


def render_md(text: str) -> str:
    """Markdown seguro: el HTML crudo se escapa antes (una respuesta de IA o una nota pegada no puede inyectar nada)."""
    return markdown.markdown(html.escape(text or "", quote=False), extensions=["extra", "sane_lists"])


def go(url: str, msg: str | None = None) -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    return RedirectResponse(url + (f"{sep}ok={quote(msg)}" if msg else ""), status_code=303)


def page(request: Request, template: str, active: str, **kw):
    with db.session() as con:
        badges = {
            "inbox": con.execute("SELECT COUNT(*) FROM inbox WHERE status='new'").fetchone()[0],
            "correo": con.execute("SELECT COUNT(*) FROM emails WHERE needs_claude=1 AND reviewed=0").fetchone()[0],
            "tareas": con.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('todo','doing','blocked') AND due_date < date('now')").fetchone()[0],
            "sistema": con.execute("SELECT COUNT(*) FROM alerts WHERE acknowledged=0").fetchone()[0],
        }
    host = request.headers.get("host", "")
    ctx = {"nav": list(NAV), "active": active, "badges": badges, "flash": request.query_params.get("ok"), "today": date.today().isoformat(),
           "pin_missing": not sec.pin_is_set(), "remote": not sec.is_local_host(host), "has_pin": sec.pin_is_set(), **kw}
    pend = 0 if ctx["remote"] else onboarding.pending()
    ctx["start_pending"] = pend
    badges["start"] = pend
    if not pend:
        ctx["nav"] = [n for n in ctx["nav"] if n[1] != "start"]
    if ctx["remote"]:  # desde fuera del PC el menú solo muestra lo permitido
        allowed = sec.config()["remote_allowed"]
        ctx["nav"] = [n for n in NAV if (n[1] != "start") and ("*" in allowed or sec.path_matches(n[3], allowed))]  # el asistente de instalación solo se muestra en el PC
    return T.TemplateResponse(request, template, ctx)


def rows(sql: str, *args) -> list[dict]:
    with db.session() as con:
        return [dict(r) for r in con.execute(sql, args)]


def projects_list() -> list[dict]:
    return rows("SELECT id, name, area, entity, status FROM projects WHERE status!='done' ORDER BY name")


# ------------------------------------------------------------------ HOY
@app.get("/")
def hoy(request: Request):
    with db.session() as con:
        g = briefing.gather(con)
        h = con.execute("SELECT COUNT(*) FROM tasks WHERE status='done' AND date(done_at)=date('now')").fetchone()[0]
        load = planner.load(con)
        today_d = date.today()
        blocks = [i for i in agenda_mod.items(con, today_d, today_d, {"bloque", "evento"}) if i["time"]]
        spark = services.done_by_day(con, 14)
    top = max([s["n"] for s in spark] + [1])
    return page(request, "hoy.html", "hoy", g=g, done_today=h, projects=projects_list(), load=load, blocks=blocks, spark=spark, spark_top=top,
                nice=views.nice(today_d))


@app.post("/habits/{habit_id}/toggle")
def habit_toggle(habit_id: int):
    with db.session() as con:
        life.toggle_habit(con, habit_id)
    return go("/")


# ------------------------------------------------------------------ INBOX
@app.get("/inbox")
def inbox(request: Request):
    triage.run()  # sugerencias al instante (determinista, sin IA)
    items = rows("SELECT id, text, source, captured_at, suggestion FROM inbox WHERE status='new' ORDER BY id")
    for it in items:
        it["s"] = json.loads(it["suggestion"]) if it["suggestion"] else None
    return page(request, "inbox.html", "inbox", items=items, projects=projects_list())


@app.post("/inbox/add")
def inbox_add(text: str = Form(...), back: str = Form("/inbox")):
    with db.session() as con:
        services.add_inbox(con, text, "portal", ACTOR)
    return go(back, "Capturado")


@app.post("/inbox/{item_id}/resolve")
def inbox_resolve(item_id: int, kind: str = Form(...), title: str = Form(""), project: str = Form(""), due: str = Form(""), prio: int = Form(3)):
    with db.session() as con:
        services.resolve_inbox(con, item_id, kind, title or None, project or None, due or None, prio, ACTOR)
    return go("/inbox", {"task": "Convertido en tarea", "note": "Guardado como nota", "discard": "Descartado"}[kind])


# ------------------------------------------------------------------ TAREAS
TASK_VIEWS = [("proximo", "Próximo"), ("cualquier", "Cualquier momento"), ("algun-dia", "Algún día"), ("tablero", "Tablero"), ("matriz", "Matriz"),
              ("todas", "Todas"), ("completadas", "Completadas")]


@app.get("/api/quick/parse")
def api_quick_parse(text: str = ""):
    """Vista previa de la captura rápida: qué entendió el sistema (no guarda nada)."""
    with db.session() as con:
        names = [r["name"] for r in con.execute("SELECT name FROM projects WHERE status!='done'")]
    q = quick.parse_quick(text, names)
    return JSONResponse({"title": q["title"], "summary": quick.describe(q)})


@app.get("/tasks")
def tasks(request: Request, view: str = "", status: str = "", project: str = "", tag: str = ""):
    if status == "done":
        view = "completadas"
    view = view or ("todas" if (project or tag) else "proximo")
    if view not in dict(TASK_VIEWS):
        view = "proximo"
    with db.session() as con:
        data = views.task_view(con, view, project, tag)
        cloud = services.tag_cloud(con)
    return page(request, "tasks.html", "tareas", view=view, views=TASK_VIEWS, data=data, project=project, tag=tag, projects=projects_list(), cloud=cloud)


@app.post("/tasks/quick")
def task_quick(text: str = Form(...), back: str = Form("/tasks")):
    with db.session() as con:
        tid, q = services.add_quick(con, text, "portal", ACTOR)
    return go(back, f"Tarea #{tid} creada · {quick.describe(q) or 'sin fecha'}")


@app.post("/tasks/add")
def task_add(title: str = Form(...), project: str = Form(""), due: str = Form(""), prio: int = Form(3), entity: str = Form("personal"),
             repeat: str = Form(""), tags: str = Form(""), back: str = Form("/tasks")):
    with db.session() as con:
        services.add_task(con, title, project or None, due or None, None, prio, entity, "", repeat or None, "portal", ACTOR, tags)
    return go(back, "Tarea creada")


@app.get("/tasks/{task_id}")
def task_detail(request: Request, task_id: int):
    with db.session() as con:
        t = services.task_row(con, task_id)
        if not t:
            return RedirectResponse("/tasks", status_code=303)
        subs = services.subtasks(con, task_id)
    from datetime import timedelta
    return page(request, "task.html", "tareas", t=t, subs=subs, projects=projects_list(), tomorrow=(date.today() + timedelta(days=1)).isoformat())


@app.post("/tasks/{task_id}/edit")
def task_edit(task_id: int, title: str = Form(...), project: str = Form(""), priority: int = Form(3), due_date: str = Form(""), scheduled_date: str = Form(""),
              start_time: str = Form(""), est_minutes: str = Form(""), recurrence: str = Form(""), status: str = Form("todo"), tags: str = Form(""),
              entity: str = Form("personal"), notes: str = Form(""), someday: str = Form("")):
    with db.session() as con:
        services.update_task(con, task_id, ACTOR, title=title, project=project, priority=priority, due_date=due_date, scheduled_date=scheduled_date,
                             start_time=start_time, est_minutes=int(est_minutes) if est_minutes.isdigit() else None, recurrence=recurrence, status=status,
                             tags=tags, entity=entity, notes=notes, someday=1 if someday else 0)
    return go(f"/tasks/{task_id}", "Cambios guardados")


@app.post("/tasks/{task_id}/status")
def task_status(task_id: int, status: str = Form(...), back: str = Form("/tasks")):
    if status not in ("todo", "doing", "blocked"):
        return go(back)
    with db.session() as con:
        services.update_task(con, task_id, ACTOR, status=status)
    return go(back, "Movida")


@app.post("/tasks/{task_id}/subtask")
def task_subtask(task_id: int, title: str = Form(...)):
    with db.session() as con:
        services.add_subtask(con, task_id, title, ACTOR)
    return go(f"/tasks/{task_id}", "Paso añadido")


@app.post("/tasks/{task_id}/schedule")
def task_schedule(task_id: int, day: str = Form(...), time: str = Form(""), minutes: str = Form(""), back: str = Form("/calendar")):
    with db.session() as con:
        services.schedule_task(con, task_id, day, time or None, int(minutes) if minutes.isdigit() else None, ACTOR)
    return go(back, "Programada")


@app.post("/tasks/{task_id}/unschedule")
def task_unschedule(task_id: int):
    with db.session() as con:
        services.unschedule_task(con, task_id, ACTOR)
    return go(f"/tasks/{task_id}", "Quitada del calendario")


@app.post("/tasks/{task_id}/focus")
def task_focus(task_id: int, minutes: int = Form(...), back: str = Form("/tasks")):
    with db.session() as con:
        services.log_focus(con, task_id, max(1, min(minutes, 240)), ACTOR)
    return go(back, f"{minutes} min de enfoque guardados")


@app.post("/tasks/{task_id}/done")
def task_done(task_id: int, back: str = Form("/tasks")):
    with db.session() as con:
        _, nxt = services.done_task(con, task_id, ACTOR)
    return go(back, "Hecha ✓" + (" · siguiente programada" if nxt else ""))


@app.post("/tasks/{task_id}/drop")
def task_drop(task_id: int, back: str = Form("/tasks")):
    with db.session() as con:
        services.drop_task(con, task_id, ACTOR)
    return go(back, "Descartada")


@app.post("/tasks/{task_id}/move")
def task_move(task_id: int, to: str = Form(...), back: str = Form("/tasks")):
    with db.session() as con:
        services.move_task(con, task_id, to, "due", ACTOR)
        con.execute("UPDATE tasks SET scheduled_date=CASE WHEN scheduled_date IS NOT NULL THEN ? ELSE scheduled_date END WHERE id=?", (to, task_id))
    return go(back, "Reprogramada")


# ------------------------------------------------------------------ CALENDARIO
CAL_VIEWS = [("month", "Mes"), ("week", "Semana"), ("day", "Día"), ("agenda", "Agenda"), ("timeline", "Línea de tiempo")]


@app.get("/calendar")
def calendar_page(request: Request, view: str = "", d: str = "", kinds: str = "", months: int = 6):
    from datetime import timedelta
    if not view:  # en el celular abre la agenda (lista); en el PC, el mes
        view = "agenda" if "mobi" in request.headers.get("user-agent", "").lower() else "month"
    view = view if view in dict(CAL_VIEWS) else "month"
    try:
        cur = date.fromisoformat(d) if d else date.today()
    except ValueError:
        cur = date.today()
    on = {k for k in kinds.split(",") if k in agenda_mod.KINDS} or set(agenda_mod.KINDS)
    kset = None if on == set(agenda_mod.KINDS) else on
    toggles = {k: ",".join(sorted((on - {k}) if k in on else (on | {k}))) for k in agenda_mod.KINDS}
    ctx = dict(view=view, views=CAL_VIEWS, cur=cur.isoformat(), kinds=agenda_mod.KINDS, on=on, toggles=toggles, kq=(f"&kinds={','.join(sorted(on))}" if kset else ""),
               months=months if months in (3, 6, 12) else 6, month=None, w=None, agenda=None, timeline=None, backlog=[])
    with db.session() as con:
        if view == "month":
            ctx["month"] = views.month_ctx(con, cur, kset)
            ctx["nav"] = ctx["month"]
        elif view in ("week", "day"):
            ctx["w"] = views.week_ctx(con, cur, 7 if view == "week" else 1, kset)
            ctx["nav"] = ctx["w"]
            ctx["backlog"] = views.backlog(con)
        elif view == "agenda":
            ctx["agenda"] = views.agenda_ctx(con, cur, 30, kset)
            ctx["nav"] = ctx["agenda"]
        else:
            ctx["timeline"] = views.timeline_ctx(con, date.today(), ctx["months"])
            ctx["nav"] = {"title": f"Próximos {ctx['months']} meses"}
    cnav = ctx.pop("nav")  # el menú lateral usa `nav`; la barra del calendario va en `cnav`
    return page(request, "calendar.html", "calendario", cnav=cnav, **ctx)


# ------------------------------------------------------------------ DISPOSITIVOS (celular, tableta, otro Windows)
def _public_base(request: Request) -> str:
    hosts = sec.config()["allowed_hosts"]
    return f"https://{hosts[0]}" if hosts else str(request.base_url).rstrip("/")


@app.get("/devices")
def devices_page(request: Request, token: str = ""):
    link = qr = None
    if token:
        import segno
        link = f"{_public_base(request)}/pair/{token}"
        qr = segno.make(link, error="m").svg_inline(scale=5, dark="#1b1e2b", light="#ffffff", border=2)
    return page(request, "devices.html", "dispositivos", devices=sec.devices(), link=link, qr=qr, minutes=sec.config()["pair_minutes"], has_host=bool(sec.config()["allowed_hosts"]))


@app.post("/devices/panel-link")
def devices_panel_link(request: Request):
    """Enlace de emparejamiento del panel fuera del PC (la llave de cifrado va en el #fragmento; se avisa por Telegram)."""
    import segno
    from pos import panel as panel_mod
    try:
        link = panel_mod.pair_link()
    except ValueError as e:
        return go("/devices", str(e))
    qr = segno.make(link, error="m").svg_inline(scale=5, dark="#1b1e2b", light="#ffffff", border=2)
    return page(request, "devices.html", "dispositivos", devices=sec.devices(), link=link, qr=qr, minutes=0, has_host=True, panel_link=True)


@app.post("/devices/new")
def devices_new():
    return RedirectResponse(f"/devices?token={sec.create_pair_token()}", status_code=303)


@app.post("/devices/{dev}/revoke")
def devices_revoke(dev: str):
    sec.revoke_device(dev)
    return go("/devices", "Dispositivo desconectado")


@app.get("/pair/{token}")
def pair(request: Request, token: str):
    """Empareja este dispositivo: el enlace es de un solo uso y vence en pocos minutos; después, el dispositivo entra solo durante un año."""
    if not sec.consume_pair_token(token):
        return T.TemplateResponse(request, "login.html", {"error": "Este enlace ya se usó o venció. Pide uno nuevo en el PC (menú Dispositivos) o con /dispositivo en Telegram.", "next": "/", "mode": "login"}, status_code=410)
    ua = request.headers.get("user-agent", "")
    dev = sec.register_device(sec.guess_label(ua), ua)
    local = sec.is_local_host(request.headers.get("host", ""))
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("pos_session", sec.sign(sec.new_session(dev=dev)), httponly=True, samesite="strict", secure=not local, max_age=sec.session_seconds(local))
    return resp


@app.get("/sw.js")
def service_worker():
    """El service worker debe servirse desde la raíz para cubrir todo el portal (app instalable con copia sin conexión)."""
    return FileResponse(HERE / "static" / "sw.js", media_type="application/javascript", headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


@app.get("/review")
def review_page(request: Request):
    with db.session() as con:
        r = review_mod.build(con)
    return page(request, "review.html", "revision", r=r)


@app.get("/plan/day")
def plan_day(request: Request, d: str = ""):
    from datetime import datetime, timedelta
    try:
        day = date.fromisoformat(d) if d else date.today()
    except ValueError:
        day = date.today()
    with db.session() as con:
        s = planner.suggest(con, day, datetime.now())
        load = planner.load(con, day)
        moves = planner.rebalance(con, day - timedelta(days=day.weekday()))
    return page(request, "planday.html", "calendario", day=day.isoformat(), label=views.nice(day), load=load, plan=s["plan"], left_out=s["left_out"], moves=moves,
                tomorrow=(day + timedelta(days=1)).isoformat())


@app.post("/plan/day/apply")
def plan_day_apply(day: str = Form(...), pick: list[str] = Form(default=[])):
    plan = []
    for p in pick:
        tid, start, mins = p.split("|")
        plan.append({"id": int(tid), "start": start, "minutes": int(mins)})
    with db.session() as con:
        n = planner.apply(con, plan, date.fromisoformat(day), ACTOR)
    return go(f"/calendar?view=day&d={day}", f"{n} bloques programados")


# ------------------------------------------------------------------ PROYECTOS / OBJETIVOS
@app.get("/projects")
def projects(request: Request):
    ps = rows("SELECT p.*, g.title AS goal, (SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id AND t.status IN ('todo','doing','blocked')) AS open_tasks, "
              "(SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id AND t.status='done') AS done_tasks "
              "FROM projects p LEFT JOIN goals g ON g.id=p.goal_id WHERE p.status!='done' ORDER BY p.status, p.name")
    with db.session() as con:
        hab = life.habit_status(con)
    for p in ps:
        p["next"] = rows("SELECT id, title, due_date FROM tasks WHERE project_id=? AND status IN ('todo','doing','blocked') AND parent_id IS NULL "
                         "ORDER BY priority, COALESCE(due_date,'9999') LIMIT 3", p["id"])
        p["habits"] = [h for h in hab if h["project_id"] == p["id"]]
        p["focus_min"] = rows("SELECT COALESCE(SUM(actual_minutes),0) AS m FROM tasks WHERE project_id=?", p["id"])[0]["m"]
    return page(request, "projects.html", "proyectos", ps=ps)


@app.post("/projects/add")
def project_add(name: str = Form(...), area: str = Form(""), entity: str = Form("personal"), milestone: str = Form(""), due: str = Form(""),
                tags: str = Form("")):
    with db.session() as con:
        services.add_project(con, name, entity, area, due or None, milestone, ACTOR, tags)
    return go("/projects", "Proyecto creado")


@app.post("/projects/{pid}/status")
def project_status(pid: int, status: str = Form(...)):
    with db.session() as con:
        services.set_project_status(con, pid, status, ACTOR)
    return go("/projects", "Estado actualizado")


@app.get("/goals")
def goals(request: Request):
    gs = rows("SELECT g.*, (SELECT COUNT(*) FROM projects p WHERE p.goal_id=g.id AND p.status!='done') AS projects "
              "FROM goals g WHERE g.status='active' ORDER BY CASE horizon WHEN 'year' THEN 0 WHEN 'quarter' THEN 1 ELSE 2 END, id")
    return page(request, "goals.html", "objetivos", gs=gs)


@app.post("/goals/add")
def goal_add(title: str = Form(...), horizon: str = Form("year"), due: str = Form("")):
    with db.session() as con:
        services.add_goal(con, title, horizon, due or None, ACTOR)
    return go("/goals", "Objetivo creado")


@app.post("/goals/{gid}/status")
def goal_status(gid: int, status: str = Form(...)):
    with db.session() as con:
        services.set_goal_status(con, gid, status, ACTOR)
    return go("/goals", "Actualizado")


# ------------------------------------------------------------------ CORREO
@app.get("/mail")
def mail_page(request: Request, q: str = ""):
    if q:
        try:
            found = rows("SELECT e.id, e.account, e.date, e.from_name, e.from_addr, e.subject, e.snippet, e.category FROM emails_fts f "
                         "JOIN emails e ON e.id=f.rowid WHERE emails_fts MATCH ? ORDER BY e.date DESC LIMIT 40", q)
        except Exception:
            found = []
    else:
        found = []
    todo = rows("SELECT id, account, date, from_name, from_addr, subject, snippet, category, reason FROM emails WHERE needs_claude=1 AND reviewed=0 ORDER BY date DESC LIMIT 60")
    stats = rows("SELECT category, COUNT(*) AS n FROM emails WHERE ingested_at >= datetime('now','-7 day') GROUP BY category ORDER BY n DESC")
    accounts = rows("SELECT account, COUNT(*) AS n, MAX(date) AS last FROM emails GROUP BY account")
    return page(request, "mail.html", "correo", todo=todo, found=found, q=q, stats=stats, accounts=accounts,
                profile=mail._find_profile() is not None)


@app.post("/mail/review/{eid}")
def mail_review(eid: int):
    with db.session() as con:
        con.execute("UPDATE emails SET reviewed=1 WHERE id=?", (eid,))
        db.audit(con, "mail.review", "WRITE", f"email:{eid}", actor=ACTOR)
    return go("/mail")


@app.post("/mail/review-all")
def mail_review_all():
    with db.session() as con:
        n = con.execute("UPDATE emails SET reviewed=1 WHERE needs_claude=1 AND reviewed=0").rowcount
        db.audit(con, "mail.review", "WRITE", "all", detail=str(n), actor=ACTOR)
    return go("/mail", f"{n} marcados como revisados")


@app.post("/mail/to-task/{eid}")
def mail_to_task(eid: int):
    with db.session() as con:
        e = con.execute("SELECT subject, from_name, from_addr FROM emails WHERE id=?", (eid,)).fetchone()
        services.add_task(con, f"Correo: {e['subject'][:120]}", notes=f"De {e['from_name'] or e['from_addr']}", source="mail", actor=ACTOR)
        con.execute("UPDATE emails SET reviewed=1 WHERE id=?", (eid,))
    return go("/mail", "Convertido en tarea")


@app.post("/mail/ingest")
def mail_ingest():
    return go("/mail", mail.ingest())


# ------------------------------------------------------------------ ESTUDIO / CONOCIMIENTO
@app.get("/study")
def study(request: Request):
    subjects = rows("SELECT p.id, p.name, p.status, p.next_milestone, p.due_date, "
                    "(SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id AND t.status IN ('todo','doing','blocked')) AS open_tasks "
                    "FROM projects p WHERE p.area='estudio' AND p.status!='done' ORDER BY p.name")
    deadlines = rows("SELECT t.id, t.title, t.due_date, p.name AS project FROM tasks t JOIN projects p ON p.id=t.project_id "
                     "WHERE p.area='estudio' AND t.status IN ('todo','doing','blocked') ORDER BY COALESCE(t.due_date,'9999') LIMIT 12")
    study_notes = rows("SELECT path, title FROM notes WHERE path LIKE 'estudio/%' ORDER BY mtime DESC LIMIT 12")
    cards = sum(len(notes.extract_cards(p)) for p in (notes.VAULT / "estudio").rglob("*.md")) if (notes.VAULT / "estudio").exists() else 0
    return page(request, "study.html", "estudio", subjects=subjects, deadlines=deadlines, study_notes=study_notes, cards=cards)


@app.post("/study/deck")
def study_deck():
    try:
        n, path = notes.build_deck("estudio")
    except ValueError as e:
        return go("/study", str(e))
    return FileResponse(path, filename="PERSONAL_OS-estudio.apkg", media_type="application/octet-stream")


@app.get("/knowledge")
def knowledge(request: Request, q: str = ""):
    found = []
    if q:
        try:
            with db.session() as con:
                found = [dict(r) for r in con.execute(
                    "SELECT n.path, n.title, snippet(notes_fts, 2, '[', ']', ' … ', 14) AS snippet FROM notes_fts f JOIN notes n ON n.id=f.rowid "
                    "WHERE notes_fts MATCH ? ORDER BY rank LIMIT 30", (q,))]
        except Exception:
            found = []
    recent = rows("SELECT path, title, tags FROM notes ORDER BY mtime DESC LIMIT 15")
    return page(request, "knowledge.html", "knowledge", q=q, found=found, recent=recent, areas=notes.AREAS)


@app.get("/knowledge/view")
def knowledge_view(request: Request, path: str):
    p = (notes.VAULT / path).resolve()
    if notes.VAULT.resolve() not in p.parents or not p.exists():
        return PlainTextResponse("no encontrada", status_code=404)
    body = p.read_text(encoding="utf-8")
    body = body.split("---\n", 2)[-1] if body.startswith("---\n") else body
    return page(request, "note.html", "knowledge", title=p.stem, path=path, html=render_md(body))


@app.post("/knowledge/new")
def knowledge_new(title: str = Form(...), area: str = Form("inbox"), tags: str = Form(""), body: str = Form("")):
    try:
        p = notes.create_note(title, area, tags, body)
    except ValueError as e:
        return go("/knowledge", str(e))
    notes.index_notes()
    return go(f"/knowledge/view?path={quote(str(p.relative_to(notes.VAULT)).replace(chr(92), '/'))}", "Nota creada")


# ------------------------------------------------------------------ CARRERA
@app.get("/career")
def career_page(request: Request):
    with db.session() as con:
        board = career.board(con)
    closed = rows("SELECT c.name AS company, a.role, a.status FROM applications a JOIN companies c ON c.id=a.company_id "
                  "WHERE a.status IN ('rejected','withdrawn') ORDER BY a.id DESC LIMIT 10")
    return page(request, "career.html", "carrera", board=board, closed=closed, statuses=career.STATUSES)


@app.post("/career/add")
def career_add(company: str = Form(...), role: str = Form(...), url: str = Form(""), status: str = Form("saved"),
               next_step: str = Form(""), next_step_date: str = Form("")):
    with db.session() as con:
        career.add_application(con, company, role, url, status, next_step, next_step_date or None)
    return go("/career", "Registrada (no se envía nada: tú postulas)")


@app.post("/career/{app_id}/update")
def career_update(app_id: int, status: str = Form(...), next_step: str = Form(""), next_step_date: str = Form("")):
    with db.session() as con:
        career.update_application(con, app_id, status=status, next_step=next_step or None, next_step_date=next_step_date or None)
    return go("/career", "Actualizada")


# ------------------------------------------------------------------ FINANZAS
@app.get("/money/go")
def money_go(period: str = "", entity: str = ""):
    """Un solo selector para mes («2026-09») o año completo («2026»)."""
    q = f"&entity={quote(entity)}" if entity else ""
    if re.fullmatch(r"\d{4}", period or ""):
        return RedirectResponse(f"/money/year?year={period}{q}", status_code=303)
    return RedirectResponse(f"/money?month={quote(period)}{q}" if period else (f"/money?entity={quote(entity)}" if entity else "/money"), status_code=303)


@app.get("/money/year")
def money_year(request: Request, year: int = 0, entity: str = ""):
    ent = entity or None
    with db.session() as con:
        years = finance.years_with_data(con) or [date.today().year]
        year = year or (date.today().year if date.today().year in years else years[0])
        y = finance.year_summary(con, year, ent)
    series = [{"label": m["label"], "income": m["income"], "expense": m["expense"]} for m in y["months"]]
    donut, legend = finance.donut_svg([{"name": c["name"], "value": c["value"]} for c in y["categories"][:8]], "gastos del año")
    with db.session() as con:
        months = [r[0] for r in con.execute("SELECT DISTINCT substr(date,1,7) FROM transactions ORDER BY 1 DESC LIMIT 24")]
    return page(request, "money_year.html", "finanzas", y=y, years=years, months=[m for m in months if m.startswith(str(year))], entity=entity, fmt=finance.fmt, bars=finance.bars_svg(series) if series else "", donut=donut, legend=legend)


@app.get("/money")
def money_page(request: Request, month: str = "", entity: str = ""):
    with db.session() as con:
        months = [r[0] for r in con.execute("SELECT DISTINCT substr(date,1,7) FROM transactions ORDER BY 1 DESC LIMIT 24")]
        years = finance.years_with_data(con)
        cov = stm.coverage(con, 6)
        if not month and months:  # por defecto: el mes más reciente con todos los extractos (si no hay, el último con movimientos)
            month = finance.default_month(con)
        s = money.month_summary(con, month or None, entity or None)
        complete = finance.month_complete(con, s["month"])
        subs_cost = life.monthly_cost(con)
        ent = entity or None
        series = finance.monthly_series(con, 12, ent, s["month"])
        donut, legend = finance.donut_svg([{"name": c["category"], "value": -c["total"]} for c in s["by_category"]])
        networth = finance.networth_series(con, ent)
        heat = finance.heat_grid(finance.daily_spend(con, s["month"], ent), s["month"])
        tops = finance.top_merchants(con, s["month"], ent)
        tips = finance.insights(con, s["month"], ent)
        balances = finance.latest_balances(con)
        goals_ = finance.goals(con)
        cards_ = finance.cards(con)
        comp_svg, comp_legend = finance.donut_svg(finance.composition(con), "patrimonio")
        recurring_ = finance.recurring(con)
        clarify = finance.to_clarify(con)
        tlinks = finance.transfer_links(con)
    txs = rows("SELECT t.id, t.date, a.name AS account, a.entity, t.amount, t.category, t.description FROM transactions t "
               "JOIN accounts a ON a.id=t.account_id ORDER BY t.date DESC, t.id DESC LIMIT 40")
    rate = round(100 * (s["income"] - s["expense"]) / s["income"]) if (s["income"] and complete) else None
    return page(request, "money.html", "finanzas", s=s, months=months, years=years, txs=txs, accounts=rows("SELECT * FROM accounts ORDER BY name"),
                entity=entity, subs_cost=subs_cost, budgets=rows("SELECT * FROM budgets ORDER BY category"), bars=finance.bars_svg(series), donut=donut, legend=legend,
                networth_svg=finance.line_svg(networth), networth_last=networth[-1]["total"] if networth else None, heat=heat, tops=tops, tips=tips, balances=balances,
                goals=goals_, rate=rate, fmt=finance.fmt, has_data=bool(txs), cards=cards_, comp_svg=comp_svg, comp_legend=comp_legend, cov=cov, recurring=recurring_, clarify=clarify, complete=complete, tlinks=tlinks)


@app.post("/money/balance")
def money_balance(account: str = Form(...), amount: float = Form(...), day: str = Form("")):
    try:
        with db.session() as con:
            finance.set_balance(con, account, amount, day or None)
    except ValueError as e:
        return go("/money", str(e))
    return go("/money", "Saldo guardado")


@app.post("/money/goal")
def money_goal(name: str = Form(...), target: float = Form(...), deadline: str = Form(""), account: str = Form(""), saved: float = Form(0)):
    with db.session() as con:
        acc = con.execute("SELECT id FROM accounts WHERE name=?", (account,)).fetchone() if account else None
        con.execute("INSERT INTO savings_goals(name, target, deadline, account_id, saved) VALUES (?,?,?,?,?)", (name.strip(), target, deadline or None, acc["id"] if acc else None, saved))
        db.audit(con, "money.goal", "WRITE", name, actor=ACTOR)
    return go("/money", "Meta creada")


@app.post("/money/goal/{goal_id}/saved")
def money_goal_saved(goal_id: int, saved: float = Form(...)):
    with db.session() as con:
        con.execute("UPDATE savings_goals SET saved=? WHERE id=?", (saved, goal_id))
    return go("/money", "Meta actualizada")


@app.post("/money/account")
def money_account(name: str = Form(...), kind: str = Form("bank"), entity: str = Form("personal")):
    with db.session() as con:
        con.execute("INSERT OR IGNORE INTO accounts(name, kind, entity) VALUES (?,?,?)", (name.strip(), kind, entity))
        db.audit(con, "money.account", "WRITE", name, actor=ACTOR)
    return go("/money", "Cuenta creada")


@app.post("/money/import")
async def money_import(account: str = Form(...), file: UploadFile = File(...), auto: str = Form(""), delimiter: str = Form(";"), date_col: str = Form("fecha"),
                       desc_col: str = Form("descripcion"), amount_col: str = Form(""), debit_col: str = Form(""), credit_col: str = Form("")):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:  # el extracto no queda guardado
        tmp.write(await file.read())
    try:
        with db.session() as con:
            if auto:
                added, skipped, found = finance.import_auto(con, Path(tmp.name), account)
                return go("/money", f"{added} movimientos nuevos, {skipped} repetidos · columnas detectadas: {found['columns']['date']}, {found['columns']['desc']}")
            added, skipped = money.import_file(con, Path(tmp.name), account, date_col, desc_col, amount_col or None,
                                               debit_col or None, credit_col or None, delimiter or ";")
        return go("/money", f"{added} movimientos nuevos, {skipped} repetidos")
    except Exception as e:
        return go("/money", f"No se pudo importar: {str(e)[:160]}")
    finally:
        Path(tmp.name).unlink(missing_ok=True)


@app.post("/money/categorize/{tx_id}")
def money_categorize(tx_id: int, category: str = Form(...)):
    with db.session() as con:
        con.execute("UPDATE transactions SET category=?, manual_category=1 WHERE id=?", (category.strip() or None, tx_id))
        db.audit(con, "money.categorize", "WRITE", f"tx:{tx_id}", detail=category, actor=ACTOR)
    return go("/money")


@app.post("/money/budget")
def money_budget(category: str = Form(...), amount: float = Form(...)):
    with db.session() as con:
        con.execute("INSERT INTO budgets(category, monthly_amount) VALUES (?,?) ON CONFLICT(category) DO UPDATE SET monthly_amount=excluded.monthly_amount",
                    (category.strip(), amount))
        db.audit(con, "money.budget", "WRITE", category, actor=ACTOR)
    return go("/money", "Presupuesto guardado")


# ------------------------------------------------------------------ VIDA Y DOCUMENTOS
@app.get("/manual")
def manual_page(request: Request):
    md = markdown.Markdown(extensions=["extra", "sane_lists", "toc"], extension_configs={"toc": {"toc_depth": "2-3"}})
    body = md.convert(html.escape((Path(__file__).resolve().parents[1] / "MANUAL.md").read_text(encoding="utf-8"), quote=False))
    return page(request, "manual.html", "manual", html=body, toc=md.toc)


@app.get("/life")
def life_page(request: Request):
    with db.session() as con:
        habits = life.habit_status(con)
        up = life.upcoming(con, 60)
        cost = life.monthly_cost(con)
    return page(request, "life.html", "vida", habits=habits, up=up, cost=cost,
                docs=rows("SELECT * FROM documents ORDER BY COALESCE(expires_on,'9999')"),
                subs=rows("SELECT * FROM subscriptions WHERE active=1 ORDER BY COALESCE(next_date,'9999')"),
                people=rows("SELECT * FROM people WHERE COALESCE(circle,'otros')='otros' ORDER BY name"))


@app.post("/life/habit")
def life_habit(name: str = Form(...)):
    with db.session() as con:
        life.add_habit(con, name)
    return go("/life", "Hábito creado")


@app.post("/life/doc")
def life_doc(name: str = Form(...), expires: str = Form(""), kind: str = Form(""), entity: str = Form("personal")):
    with db.session() as con:
        life.add_doc(con, name, expires or None, kind, entity)
    return go("/life", "Documento registrado")


@app.post("/life/sub")
def life_sub(name: str = Form(...), amount: float = Form(...), cycle: str = Form("monthly"), next_date: str = Form(""), entity: str = Form("personal")):
    with db.session() as con:
        life.add_sub(con, name, amount, cycle, next_date or None, "COP", entity)
    return go("/life", "Suscripción registrada")


@app.post("/life/person")
def life_person(name: str = Form(...), relation: str = Form(""), birthday: str = Form(""), email: str = Form(""), circle: str = Form("otros")):
    with db.session() as con:
        life.add_person(con, name, relation, birthday or None, email, "", circle)
    return go("/life", "Persona registrada")


@app.post("/life/person/{pid}/contacted")
def life_contacted(pid: int):
    with db.session() as con:
        con.execute("UPDATE people SET last_contact=? WHERE id=?", (date.today().isoformat(), pid))
    return go("/life", "Anotado")


# ------------------------------------------------------------------ MULTIMEDIA / DESARROLLO
@app.get("/media")
def media_page(request: Request):
    media.ensure_folders()
    out = sorted((media.MEDIA_HOME / "Salida").glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)[:12]
    return page(request, "media.html", "multimedia", home=str(media.MEDIA_HOME), actions=list(media.ACTIONS), editors=media.editors(),
                outputs=[{"name": p.name, "kb": p.stat().st_size // 1024} for p in out])


@app.post("/media/open")
def media_open(folder: str = Form("")):
    target = media.MEDIA_HOME / folder if folder in list(media.ACTIONS) + ["Salida", "Originales-procesados"] else media.MEDIA_HOME
    os.startfile(str(target))  # abre el Explorador de Windows
    return go("/media")


@app.post("/media/editor")
def media_editor(editor: str = Form(...)):
    try:
        return go("/media", f"Abriendo {media.open_editor(editor)}…")
    except RuntimeError as e:
        return go("/media", str(e))


@app.post("/media/run")
def media_run():
    threading.Thread(target=jobs.run_job, args=("media-watch",), daemon=True).start()
    return go("/media", "Procesando lo que hay en las carpetas…")


@app.get("/dev")
def dev(request: Request):
    return page(request, "dev.html", "desarrollo",
                repos=rows("SELECT * FROM repos ORDER BY dirty DESC, name"),
                checked=(rows("SELECT MAX(checked_at) AS c FROM repos")[0]["c"]))


@app.post("/dev/scan")
def dev_scan():
    from pos import repos
    return go("/dev", repos.scan())


# ------------------------------------------------------------------ PLANES
def _plan_card(con, p: dict) -> dict:
    p.update(plans.progress(con, p["id"]))
    p["next"] = [dict(r) for r in con.execute("SELECT id, title, scheduled_date, est_minutes FROM plan_items WHERE plan_id=? AND status='todo' "
                                              "ORDER BY scheduled_date, stage_id, position LIMIT 3", (p["id"],))]
    return p


@app.get("/plans")
def plans_page(request: Request):
    with db.session() as con:
        active = [_plan_card(con, dict(r)) for r in con.execute("SELECT * FROM plans WHERE status!='done' ORDER BY status, end_date")]
        finished = [dict(r) for r in con.execute("SELECT id, title, end_date FROM plans WHERE status='done' ORDER BY end_date DESC LIMIT 8")]
    g = plans.gantt([{"label": p["title"], "start": p["start_date"], "end": p["end_date"], "percent": p["percent"], "href": f"/plans/{p['id']}",
                      "cls": "bad" if p["state"] == "atrasado" else "ok" if p["percent"] == 100 else ""} for p in active])
    return page(request, "plans.html", "planes", plans_=active, gantt=g, finished=finished, projects=projects_list(), day_labels=plans.DAY_LABELS)


@app.post("/plans/create")
def plan_create(title: str = Form(...), outline: str = Form(...), kind: str = Form("curso"), start: str = Form(""), hours_per_week: float = Form(5),
                days: list[str] = Form(default=[]), project: str = Form(""), tags: str = Form(""), url: str = Form(""), entity: str = Form("personal")):
    try:
        with db.session() as con:
            pid = plans.create_plan(con, title, outline, kind, start or None, hours_per_week, ",".join(days) or "0,1,2,3,4", project or None, entity, tags, url)
    except ValueError as e:
        return go("/plans", str(e))
    return go(f"/plans/{pid}", "Plan creado y programado")


@app.get("/plans/{pid}")
def plan_detail(request: Request, pid: int):
    with db.session() as con:
        row = con.execute("SELECT * FROM plans WHERE id=?", (pid,)).fetchone()
        if not row:
            return PlainTextResponse("no encontrado", status_code=404)
        p = _plan_card(con, dict(row))
        stages = []
        for s in con.execute("SELECT * FROM plan_stages WHERE plan_id=? ORDER BY position", (pid,)).fetchall():
            items = [dict(i) for i in con.execute("SELECT * FROM plan_items WHERE stage_id=? ORDER BY position", (s["id"],))]
            done = sum(1 for i in items if i["status"] == "done")
            stages.append({**dict(s), "items": items, "done": done, "percent": round(100 * done / len(items)) if items else 0,
                           "minutes": sum(i["est_minutes"] for i in items)})
        qs = [dict(r) for r in con.execute("SELECT id, question, status FROM questions WHERE scope=? ORDER BY id DESC LIMIT 5", (f"plan:{pid}",))]
    g = plans.gantt([{"label": s["title"], "start": s["start_date"], "end": s["end_date"], "percent": s["percent"], "href": "",
                      "cls": "ok" if s["percent"] == 100 else ""} for s in stages])
    return page(request, "plan.html", "planes", p=p, stages=stages, gantt=g, qs=qs)


@app.post("/plans/items/{item_id}/toggle")
def plan_item_toggle(item_id: int, back: str = Form("/plans")):
    with db.session() as con:
        done = plans.toggle_item(con, item_id, ACTOR)
    return go(back, "Hecho ✓" if done else "Marcado como pendiente")


@app.post("/plans/{pid}/reschedule")
def plan_reschedule(pid: int):
    with db.session() as con:
        end = plans.schedule(con, pid, date.today().isoformat())
    return go(f"/plans/{pid}", f"Reprogramado: termina el {end}")


@app.post("/plans/{pid}/status")
def plan_status(pid: int, status: str = Form(...)):
    with db.session() as con:
        con.execute("UPDATE plans SET status=? WHERE id=?", (status, pid))
        db.audit(con, "plan.status", "WRITE", f"plan:{pid}", detail=status, actor=ACTOR)
    return go(f"/plans/{pid}", "Estado actualizado")


@app.get("/plans/{pid}/calendar.ics")
def plan_ics(pid: int):
    with db.session() as con:
        path = plans.export_ics(con, pid)
    return FileResponse(path, filename=f"plan-{pid}.ics", media_type="text/calendar")


# ------------------------------------------------------------------ PREGUNTAR A LA IA
@app.get("/ask")
def ask_page(request: Request, scope: str = "general"):
    qs = rows("SELECT id, question, scope, status, answer, answered_by, created_at FROM questions ORDER BY id DESC LIMIT 30")
    for q in qs:
        q["html"] = render_md(q["answer"]) if q["answer"] else ""
    return page(request, "ask.html", "preguntar", qs=qs, scope=scope, plans_=rows("SELECT id, title FROM plans WHERE status!='done' ORDER BY title"),
                ollama=ai.available())


@app.post("/ask/add")
def ask_add(question: str = Form(...), scope: str = Form("general")):
    with db.session() as con:
        qid = ask.add_question(con, question, scope)
    return go("/ask", f"Pregunta #{qid} guardada: Claude la responde cuando abras una sesión")


@app.post("/ask/{qid}/local")
def ask_local(qid: int):
    threading.Thread(target=ask.answer_local, args=(qid,), daemon=True).start()
    return go("/ask", "La IA local está respondiendo (1–3 min). Recarga en un momento.")


# ------------------------------------------------------------------ PRIMEROS PASOS
@app.get("/start")
def start_page(request: Request):
    st = onboarding.steps(force=True)
    req = [s for s in st if not s["optional"]]
    return page(request, "start.html", "start", steps=st, req_done=sum(1 for s in req if s["done"]), req_total=len(req))


def _do(fn, back="/start"):
    try:
        return go(back, fn())
    except Exception as e:
        return go(back, f"No se pudo: {str(e)[:140]}")


@app.post("/start/open-mail")
def start_open_mail():
    return _do(onboarding.open_mail)


@app.post("/start/mail-autostart")
def start_mail_autostart():
    return _do(onboarding.enable_mail_autostart)


@app.post("/start/open-backup")
def start_open_backup():
    return _do(onboarding.open_backup)


@app.post("/start/install-editors")
def start_install_editors():
    return _do(onboarding.install_editors)


@app.post("/start/copy-key")
def start_copy_key():
    return _do(onboarding.copy_backup_key)


@app.post("/start/mark/{key}")
def start_mark(key: str):
    return _do(lambda: (onboarding.set_flag(key), "Anotado")[1])


@app.get("/start/telegram")
def start_telegram(request: Request):
    bot = None
    if telegram._token():
        try:
            bot = telegram.api("getMe")["result"]["username"]
        except Exception:
            bot = None
    return page(request, "start_telegram.html", "start", bot=bot, has_token=bool(telegram._token()), connected=telegram.configured())


@app.post("/start/telegram/token")
def start_telegram_token(token: str = Form(...)):
    try:
        user = telegram.begin_setup(token)
        return go("/start/telegram", f"Bot @{user} verificado. Ahora escríbele /start desde tu celular.")
    except Exception as e:
        return go("/start/telegram", f"No se pudo: {str(e)[:140]}")


@app.post("/start/telegram/verify")
def start_telegram_verify():
    try:
        name = telegram.finish_setup()
        onboarding._cache["t"] = 0
        return go("/start", f"Telegram conectado con el chat de {name}")
    except Exception as e:
        return go("/start/telegram", str(e)[:160])


# ------------------------------------------------------------------ AUTOMATIZACIONES / SISTEMA
_sched_cache: dict = {"t": 0, "v": {}}


def scheduler_info() -> dict:
    """Próxima/última ejecución de cada job según el Programador de Windows (fuente única del horario)."""
    if time.time() - _sched_cache["t"] < 60:
        return _sched_cache["v"]
    cmd = ("Get-ScheduledTask -TaskPath '\\PERSONAL_OS\\' | ForEach-Object { $i = $_ | Get-ScheduledTaskInfo; "
           "[pscustomobject]@{n=$_.TaskName; next=$i.NextRunTime; last=$i.LastRunTime; res=$i.LastTaskResult} } | ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=25)
        data = json.loads(r.stdout) if r.stdout.strip() else []
        data = data if isinstance(data, list) else [data]
        v = {d["n"]: d for d in data}
    except Exception:
        v = {}
    _sched_cache.update(t=time.time(), v=v)
    return v


@app.get("/jobs")
def jobs_page(request: Request):
    info = scheduler_info()
    items = []
    with db.session() as con:
        for name in sorted(jobs.REGISTRY):
            r = con.execute("SELECT started_at, status, message FROM job_runs WHERE job=? ORDER BY id DESC LIMIT 1", (name,)).fetchone()
            s = info.get(name, {})
            nxt = s.get("next") if isinstance(s.get("next"), str) else None
            items.append({"name": name, "status": r["status"] if r else "never", "last": r["started_at"] if r else None,
                          "message": (r["message"] or "")[:140] if r else "", "scheduled": bool(s), "manual": name in ("backup-essential", "backup-full")})
    return page(request, "jobs.html", "automatizaciones", items=items)


@app.post("/jobs/{name}/run")
def job_run(name: str):
    if name not in jobs.REGISTRY or name in ("backup-essential", "backup-full"):
        return go("/jobs", "Ese job se lanza con doble clic (disco externo)")
    threading.Thread(target=jobs.run_job, args=(name,), daemon=True).start()
    return go("/jobs", f"{name} lanzado")


@app.get("/system")
def system(request: Request):
    with db.session() as con:
        health = briefing.latest_health(con)
        alerts = [dict(r) for r in con.execute("SELECT * FROM alerts WHERE acknowledged=0 ORDER BY id DESC LIMIT 30")]
        audit = [dict(r) for r in con.execute("SELECT ts, actor, permission, action, target, result FROM audit_log ORDER BY id DESC LIMIT 15")]
        failed = [dict(r) for r in con.execute("SELECT job, started_at, message FROM job_runs WHERE status='failed' ORDER BY id DESC LIMIT 5")]
    return page(request, "system.html", "sistema", health={h["metric"]: h for h in health}, alerts=alerts, audit=audit, failed=failed,
                ollama=ai.available())


@app.post("/alerts/{aid}/ack")
def alert_ack(aid: int, back: str = Form("/system")):
    with db.session() as con:
        con.execute("UPDATE alerts SET acknowledged=1 WHERE id=?", (aid,))
    return go(_safe_next(back))


@app.post("/alerts/ack-all")
def alert_ack_all():
    with db.session() as con:
        con.execute("UPDATE alerts SET acknowledged=1")
    return go("/system", "Alertas archivadas")


