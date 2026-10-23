"""Telegram: alertas al celular y captura entrante (texto y notas de voz → inbox). Token en Credential Manager.
Solo responde al chat autorizado durante `setup`; cualquier otro remitente se ignora."""
import json
import re
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import keyring
import typer

from . import db

app = typer.Typer(help="Telegram (alertas y captura)", no_args_is_help=True)
SERVICE = "pos"


def _token() -> str | None:
    return keyring.get_password(SERVICE, "telegram-token")


def _chat() -> str | None:
    return keyring.get_password(SERVICE, "telegram-chat")


def configured() -> bool:
    return bool(_token() and _chat())


def api(method: str, params: dict | None = None, token: str | None = None, timeout: int = 40) -> dict:
    url = f"https://api.telegram.org/bot{token or _token()}/{method}"
    data = urllib.parse.urlencode(params or {}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data), timeout=timeout) as r:
        return json.loads(r.read())


def send(text: str, buttons: list[list[tuple[str, str]]] | None = None) -> bool:
    """Envía un mensaje; `buttons` = filas de (texto, dato) que salen como botones en línea."""
    if not configured():
        return False
    params = {"chat_id": _chat(), "text": text[:3900]}
    if buttons:
        params["reply_markup"] = json.dumps({"inline_keyboard": [[{"text": a, "callback_data": b} for a, b in row] for row in buttons]})
    try:
        return bool(api("sendMessage", params).get("ok"))
    except Exception:
        return False


def _download(file_id: str, dest: Path) -> Path:
    info = api("getFile", {"file_id": file_id})["result"]
    urllib.request.urlretrieve(f"https://api.telegram.org/file/bot{_token()}/{info['file_path']}", dest)
    return dest


def handle_command(text: str) -> str | None:
    """Interfaz de celular por Telegram: responde a /comandos; devuelve None si el mensaje es una captura normal."""
    if not text.startswith("/"):
        return None
    cmd, _, arg = text.partition(" ")
    cmd, arg = cmd.lower().split("@")[0], arg.strip()
    from . import ask, briefing, plans, tgfmt
    with db.session() as con:
        if cmd in ("/ayuda", "/help", "/start", "/menu"):
            return tgfmt.HELP
        if cmd == "/hoy":
            return tgfmt.morning(briefing.gather(con))
        if cmd == "/dia":
            g = briefing.gather(con)
            L = [f"🕘 Tus bloques de hoy · {tgfmt.short(date.today())}"] + [f"{b['time']}{'–' + b['end_time'] if b['end_time'] else ''} · {b['title'][:58]}" for b in g["blocks"]]
            L += ["", f"Carga: {g['load']['minutes'] // 60} h {g['load']['minutes'] % 60} min de {g['load']['capacity'] // 60} h ({g['load']['pct']}%)", g["load"]["advice"]]
            return "\n".join(L) if g["blocks"] else "Sin bloques de tiempo hoy.\n" + g["load"]["advice"] + "\nPide /hoy o programa tareas en el portal (Proponer mi día)."
        if cmd == "/gastos":
            from . import finance, money
            s = money.month_summary(con, finance.default_month(con))
            if not (s["income"] or s["expense"]):
                return "Aún no hay movimientos este mes. Importa un extracto en el portal (Finanzas)."
            L = [f"💰 {s['month']}", f"Ingresos {finance.fmt(s['income'])} · Gastos {finance.fmt(s['expense'])} · Neto {finance.fmt(s['net'])}", ""]
            L += [f"• {c['category']}: {finance.fmt(-c['total'])}" + (" ⚠️" if c["over_budget"] else "") for c in s["by_category"][:5]]
            L += ["", *[("✅ " if t["level"] == "ok" else "⚠️ " if t["level"] == "warn" else "💡 ") + t["text"] for t in finance.insights(con, s["month"])[:3]]]
            return "\n".join(L)
        if cmd == "/dispositivo":
            from . import portal_security as sec
            hosts = sec.config()["allowed_hosts"]
            if not hosts:
                return "Primero configura el acceso por Tailscale (Primeros pasos → Tailscale)."
            return (f"📲 Abre este enlace en el dispositivo nuevo (celular, tableta u otro Windows) con Tailscale conectado:\nhttps://{hosts[0]}/pair/{sec.create_pair_token()}\n"
                    f"Vale {sec.config()['pair_minutes']} minutos y se usa una sola vez. Después entra solo, sin contraseña.")
        if cmd == "/semana":
            g = briefing.gather(con)
            return tgfmt.week(g["overdue"] + g["today"] + g["next7"], date.today())
        if cmd == "/alertas":
            rows = con.execute("SELECT id, level, title FROM alerts WHERE acknowledged=0 ORDER BY CASE level WHEN 'CRITICAL' THEN 0 WHEN 'IMPORTANT' THEN 1 ELSE 2 END, id DESC LIMIT 12").fetchall()
            return "\n".join(f"{tgfmt.LEVEL.get(r['level'], '🔔')[0]} #{r['id']} · {r['title'][:90]}" for r in rows) + "\n\n/ok N para archivar" if rows else "Sin alertas abiertas ✅"
        if cmd == "/ok" and arg.isdigit():
            con.execute("UPDATE alerts SET acknowledged=1 WHERE id=?", (int(arg),))
            return f"👌 Alerta #{arg} archivada"
        if cmd == "/hecho" and arg.isdigit():
            return _done(con, int(arg))
        if cmd == "/pospon" and arg:
            from . import services, triage
            tid, _, when = arg.partition(" ")
            due = triage.parse_due(when or "mañana")
            if not tid.isdigit() or not due:
                return "Uso: /pospon 12 mañana  (o viernes, 15 de octubre, en 3 días)"
            return f"⏰ Tarea #{tid} movida al {tgfmt.short(due)}" if services.move_task(con, int(tid), due, actor="telegram") else "No encontré esa tarea abierta"
        if cmd == "/tarea" and arg:
            from . import services, triage
            due, title = _split_due(arg, triage)
            tid, q = services.add_quick(con, arg, "telegram", "telegram")
            from . import quick
            return f"➕ Tarea #{tid}: {q['title'][:70]}" + (f"\n{quick.describe(q)}" if quick.describe(q) else "")
        if cmd == "/tareas":
            rows = con.execute("SELECT id, title, due_date FROM tasks WHERE status IN ('todo','doing','blocked') ORDER BY priority, COALESCE(due_date,'9999') LIMIT 12").fetchall()
            return "\n".join(f"☐ {r['id']} · {r['title'][:70]}" + (f" ({tgfmt.short(r['due_date'])})" if r["due_date"] else "") for r in rows) + "\n\n✅ /hecho N" if rows else "Sin tareas abiertas 🎉"
        if cmd == "/plan":
            ps = con.execute("SELECT id, title FROM plans WHERE status='active' ORDER BY end_date").fetchall()
            out = [f"📚 {p['title']}: {plans.progress(con, p['id'])['percent']}% · {plans.progress(con, p['id'])['state']}" for p in ps]
            today = plans.plan_today(con)
            out += ["", "Hoy:"] + [f"• {i['title']} ({i['est_minutes']} min)" for i in today[:8]] if today else []
            return "\n".join(out) or "Aún no hay planes."
        if cmd == "/buscar" and arg:
            hits = [f"📝 {r['title']}" for r in con.execute("SELECT n.title FROM notes_fts f JOIN notes n ON n.id=f.rowid WHERE notes_fts MATCH ? LIMIT 5", (" OR ".join(arg.split()),))] \
                if arg else []
            mails = [f"✉ {r['subject'][:70]}" for r in con.execute("SELECT e.subject FROM emails_fts f JOIN emails e ON e.id=f.rowid WHERE emails_fts MATCH ? ORDER BY e.date DESC LIMIT 5", (" OR ".join(arg.split()),))]
            return "\n".join(hits + mails) or "Sin resultados."
        if cmd == "/coursera" and arg.lower() in ("si", "sí", "no"):
            from . import coursera
            return coursera.apply_proposal(con) if arg.lower() in ("si", "sí") else coursera.discard_proposal(con)
        if cmd == "/pregunta" and arg:
            return f"Pregunta #{ask.add_question(con, arg)} guardada. Claude la responde en su próxima sesión."
    return tgfmt.HELP


_DATE_START = re.compile(r"^(hoy|mañana|pasado|en|el|para|lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado|domingo|\d{1,2}|pr[óo]ximo|este|esta)$", re.I)


def _split_due(arg: str, triage) -> tuple[str | None, str]:
    """«comprar leche viernes» → (fecha del viernes, «comprar leche»). Mira las últimas 1–3 palabras si empiezan como una fecha."""
    words = arg.split()
    for n in (3, 2, 1):
        if len(words) > n and _DATE_START.match(words[-n]):
            due = triage.parse_due(" ".join(words[-n:]))
            if due:
                return due, " ".join(words[:-n])
    return None, arg


def _done(con, task_id: int) -> str:
    from . import services, tgfmt
    row = con.execute("SELECT title FROM tasks WHERE id=? AND status IN ('todo','doing','blocked')", (task_id,)).fetchone()
    ok, nxt = services.done_task(con, task_id, actor="telegram")
    if not ok:
        return "No encontré esa tarea abierta"
    today = date.today().isoformat()
    left = con.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('todo','doing','blocked') AND due_date<=?", (today,)).fetchone()[0]
    return f"✔ Hecha: {row['title'][:70]}" + (f"\n🔁 siguiente #{nxt}" if nxt else "") + (f"\n☐ Te quedan {left} para hoy o vencidas (/hoy)" if left else "\n🎉 Nada más para hoy")


def handle_callback(data: str) -> str:
    """Botones en línea: done:ID · snooze:ID · ok:ID · coursera:si|no. Devuelve el texto de confirmación."""
    kind, _, arg = data.partition(":")
    if kind == "done" and arg.isdigit():
        with db.session() as con:
            return _done(con, int(arg))
    if kind == "snooze" and arg.isdigit():
        return handle_command(f"/pospon {arg} mañana") or ""
    if kind == "ok" and arg.isdigit():
        return handle_command(f"/ok {arg}") or ""
    if kind == "coursera":
        return handle_command(f"/coursera {arg}") or ""
    return "Acción desconocida"


def capture() -> str:
    """Job: trae mensajes nuevos del chat autorizado y los guarda en el inbox (la voz se transcribe localmente)."""
    if not configured():
        return "Telegram sin configurar (pos telegram setup)"
    with db.session() as con:
        row = con.execute("SELECT value FROM meta WHERE key='telegram_offset'").fetchone()
    offset = int(row["value"]) if row else 0
    try:
        updates = api("getUpdates", {"offset": offset, "timeout": 0}).get("result", [])
    except OSError as e:  # sin internet o Telegram caído: no es un fallo del sistema; el próximo ciclo lo recupera
        return f"sin conexión con Telegram ({type(e).__name__}); se reintenta"
    added = 0
    for u in updates:
        offset = u["update_id"] + 1
        cb = u.get("callback_query")
        if cb:  # el usuario pulsó un botón en línea
            if str(((cb.get("message") or {}).get("chat") or {}).get("id")) == _chat():
                reply = handle_callback(cb.get("data") or "")
                try:
                    api("answerCallbackQuery", {"callback_query_id": cb["id"], "text": reply.splitlines()[0][:150] if reply else "Listo"})
                except Exception:
                    pass
                send(reply)
            continue
        m = u.get("message") or {}
        if str((m.get("chat") or {}).get("id")) != _chat():
            continue
        text = m.get("text") or m.get("caption") or ""
        if m.get("voice") or m.get("audio"):
            from . import media
            f = _download((m.get("voice") or m.get("audio"))["file_id"], db.DATA_DIR / "tg-voice.ogg")
            text = media.transcribe_text(f)
            f.unlink(missing_ok=True)
        doc = m.get("document")
        if doc:  # extractos y otros archivos: se guardan sin importar, hasta que Claude los revise contigo
            from . import statements
            name = doc.get("file_name") or "archivo"
            try:
                tmp = _download(doc["file_id"], db.DATA_DIR / "tg-recibido.tmp")
                statements.hold_file(tmp, name)
                send(f"📎 Guardé «{name}». Nada se importa solo: en cuanto lo revise y lo suba te cuento. En espera: {len(statements.held())}.")
            except Exception as e:
                send(f"⚠️ No pude guardar «{name}» ({str(e)[:80]}). Si pesa más de 20 MB, suéltalo en la carpeta Documents\\PERSONAL_OS-Finanzas\\Extractos.")
            added += 1
            continue
        if not text:
            continue
        reply = handle_command(text)
        if reply is not None:
            send(reply)
            continue
        with db.session() as con:
            cur = con.execute("INSERT INTO inbox(text, source) VALUES (?,?)", (text, "telegram"))
            db.audit(con, "inbox.add", "WRITE", f"inbox:{cur.lastrowid}", detail="telegram", actor="job")
        send(f"✓ capturado #{cur.lastrowid}")
        added += 1
    with db.session() as con:
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('telegram_offset', ?)", (str(offset),))
    return f"{added} capturas nuevas"


def begin_setup(token: str) -> str:
    """Paso 1: valida el token con Telegram y lo guarda (Credential Manager). Devuelve el usuario del bot."""
    token = token.strip()
    me = api("getMe", token=token)
    if not me.get("ok"):
        raise ValueError("Token inválido: cópialo de nuevo desde @BotFather")
    keyring.set_password(SERVICE, "telegram-token", token)
    keyring.delete_password(SERVICE, "telegram-chat") if _chat() else None
    with db.session() as con:
        db.audit(con, "telegram.token", "SENSITIVE_ACTION", "telegram", detail=f"bot @{me['result']['username']}")
    return me["result"]["username"]


def finish_setup() -> str:
    """Paso 2: tras escribirle /start al bot, toma ese chat como el único autorizado. Devuelve el nombre."""
    token = _token()
    if not token:
        raise ValueError("Primero pega el token del bot")
    ups = api("getUpdates", token=token).get("result", [])
    chats = [(u["message"]["chat"]["id"], u["message"]["chat"].get("first_name", "")) for u in ups if u.get("message")]
    if not chats:
        raise ValueError("Aún no llega ningún mensaje: escríbele /start al bot y vuelve a verificar")
    chat_id, name = chats[-1]
    keyring.set_password(SERVICE, "telegram-chat", str(chat_id))
    with db.session() as con:
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('telegram_offset', ?)", (str(max(u["update_id"] for u in ups) + 1),))
        db.audit(con, "telegram.setup", "SENSITIVE_ACTION", "telegram", detail=f"chat de {name}")
    send("✅ PERSONAL_OS conectado. Escríbeme aquí ideas o pendientes (también notas de voz) y llegan a tu inbox. /ayuda muestra los comandos.")
    set_menu()
    return name


def set_menu() -> bool:
    """Deja el menú de comandos de Telegram (el botón «/» junto al teclado)."""
    cmds = [("hoy", "Tu día: qué hacer ahora"), ("dia", "Tus bloques de tiempo de hoy"), ("semana", "Próximos 7 días"), ("tareas", "Pendientes con casilla"), ("alertas", "Avisos abiertos"),
            ("tarea", "Crear: /tarea comprar leche viernes"), ("gastos", "Resumen de finanzas del mes"), ("dispositivo", "Enlace para añadir un celular/tableta"), ("plan", "Avance de tus planes"), ("ayuda", "Todos los comandos")]
    try:
        return bool(api("setMyCommands", {"commands": json.dumps([{"command": c, "description": d} for c, d in cmds])}).get("ok"))
    except Exception:
        return False


@app.command("menu")
def menu_cmd() -> None:
    """Actualiza el menú de comandos del bot en Telegram."""
    typer.echo("menú actualizado" if set_menu() else "no se pudo actualizar (¿Telegram sin configurar o sin internet?)")


@app.command("setup")
def setup() -> None:
    """Configura el bot: pegas el token (lo da @BotFather) y escribes /start al bot desde tu celular."""
    try:
        user = begin_setup(typer.prompt("Token del bot (de @BotFather)", hide_input=True))
        typer.echo(f"Bot: @{user}. Abre Telegram y envíale /start ahora…")
        typer.prompt("Cuando lo hayas enviado, pulsa Enter", default="", show_default=False)
        typer.echo(f"Listo: chat de {finish_setup()} autorizado.")
    except ValueError as e:
        raise typer.Exit(str(e))


@app.command("send")
def send_cmd(text: str) -> None:
    typer.echo("enviado" if send(text) else "no configurado o falló")


@app.command("capture")
def capture_cmd() -> None:
    typer.echo(capture())
