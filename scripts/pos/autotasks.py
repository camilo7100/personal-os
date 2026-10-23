"""Tareas que el sistema crea solo cuando algo de tu vida lo exige (para que nada dependa de acordarte):
  · Regalo para familia y pareja: a 30 días de un cumpleaños, aniversario, San Valentín, Día de la Madre… crea «Pensar y comprar el regalo» con tiempo de sobra.
  · Documentos que vencen (SOAT, revisión técnico-mecánica, pasaporte…): a 30 días crea «Preparar …» con lo que hay que tener listo, y a 15 días «Renovar …».
    Al marcar «Renovar …» como hecha, el documento pasa solo a vencer un año después y el ciclo se repite cada año.
Idempotente: cada una se crea una sola vez (clave en `external_items`); si la haces, la descartas o la borras, no vuelve a aparecer."""
import re
from datetime import date, timedelta

from . import dates, db

SOURCE = "auto"
CREATED: list[tuple[str, str, str]] = []  # (nivel, título, detalle) de lo creado en esta corrida: el motor de avisos lo manda al celular

# Qué preparar y qué hacer por tipo de documento (el primero cuyo patrón coincida con el nombre)
PLAYBOOK = [
    (r"soat", ("🛠️ Preparar la renovación del SOAT", "Cotiza en 2 o 3 aseguradoras (la tarifa es la misma por ley: compara descuentos y forma de pago). Ten a mano documento, placa y tarjeta de propiedad, "
                                                 "confirma la fecha exacta en el RUNT y que la tarjeta con la que pagas tenga cupo.",
               "📄 Comprar el SOAT", "Cómpralo hoy: la nueva vigencia empieza al día siguiente del vencimiento. Guarda el PDF en Vida y documentos y marca esta tarea como hecha (la fecha del documento se actualiza sola).")),
    (r"t[eé]cnico|mec[aá]nica|rtm", ("🛠️ Preparar la revisión técnico-mecánica", "Revisa luces, llantas, frenos, limpiabrisas, líquidos, pito y cinturones. Ten el SOAT vigente y la tarjeta de propiedad, "
                                                                                    "y agenda la cita en el CDA (el del año pasado: CDA). Se puede hacer hasta ~30 días antes sin perder la fecha.",
                                      "🚗 Hacer la revisión técnico-mecánica", "Hazla esta semana con cita en el CDA. Guarda el certificado en Vida y documentos y marca esta tarea como hecha (la fecha del documento se actualiza sola).")),
]


def _playbook(name: str) -> tuple[str, str, str, str]:
    for pat, pb in PLAYBOOK:
        if re.search(pat, name.lower()):
            return pb
    return (f"🛠️ Preparar la renovación de {name}", "Revisa qué papeles, fotos o pagos necesitas y agenda el trámite.", f"📄 Renovar {name}", "Haz el trámite y marca esta tarea como hecha: la fecha del documento se actualiza sola.")


def _plus_year(iso: str) -> str:
    d = date.fromisoformat(iso)
    try:
        return d.replace(year=d.year + 1).isoformat()
    except ValueError:  # 29 de febrero
        return d.replace(year=d.year + 1, day=28).isoformat()


def _make(con, uid: str, title: str, due: date, prio: int, tags: str, minutes: int, notes: str = "") -> int:
    if con.execute("SELECT 1 FROM external_items WHERE uid=?", (uid,)).fetchone():
        return 0
    cur = con.execute("INSERT INTO tasks(title, notes, priority, due_date, source, tags, est_minutes) VALUES (?,?,?,?,?,?,?)", (title, notes, prio, due.isoformat(), SOURCE, tags, minutes))
    con.execute("INSERT INTO external_items(uid, source, task_id, due) VALUES (?,?,?,?)", (uid, SOURCE, cur.lastrowid, due.isoformat()))
    db.audit(con, "autotask", "WRITE", f"task:{cur.lastrowid}", detail=title, actor="job")
    CREATED.append(("IMPORTANT" if prio == 1 else "INFO", title, notes or f"Para el {due.isoformat()}."))
    return 1


def run(con, today: date | None = None) -> int:
    today = today or date.today()
    n = 0
    for ev in dates.circle_events(con, today, 30):
        if ev["left"] <= 0:
            continue
        when = date.fromisoformat(ev["date"])
        due = max(today, when - timedelta(days=7))  # comprar con una semana de margen
        who = f" para {ev['who']}" if ev.get("who") and ev["who"] not in ev["title"] else ""
        n += _make(con, f"gift:{ev['kind']}:{ev.get('id') or ev['who']}:{when.year}", f"🎁 Pensar y comprar el regalo{who}: {ev['title']}", due, 2, "regalo,familia", 60,
                   f"Fecha: {ev['date']}. Compra con una semana de margen.")
    n += _roll_over_renewed(con)
    for d in con.execute("SELECT id, name, expires_on FROM documents WHERE expires_on IS NOT NULL"):
        exp = date.fromisoformat(d["expires_on"])
        left = (exp - today).days
        prep_t, prep_n, do_t, do_n = _playbook(d["name"])
        if left <= 30:  # a 30 días: qué preparar
            n += _make(con, f"doc:{d['id']}:{d['expires_on']}:30", f"{prep_t} (vence {d['expires_on']})", max(today, exp - timedelta(days=15)), 2, "documentos,vencimiento", 45, prep_n)
        if left <= 15:  # a 15 días: hacerlo
            n += _make(con, f"doc:{d['id']}:{d['expires_on']}:15", f"{do_t} (vence {d['expires_on']})", max(today, exp - timedelta(days=3)), 1, "documentos,vencimiento", 60, do_n)
    return n


def _roll_over_renewed(con) -> int:
    """Cuando marcas como hecha la tarea de renovar, el documento queda venciendo un año después (así se repite cada año sin que lo configures)."""
    n = 0
    for e in con.execute("SELECT e.uid, e.task_id FROM external_items e JOIN tasks t ON t.id=e.task_id WHERE e.uid LIKE 'doc:%:15' AND t.status='done'").fetchall():
        _, doc_id, old, _ = e["uid"].split(":")
        d = con.execute("SELECT id, name, expires_on, notes FROM documents WHERE id=?", (doc_id,)).fetchone()
        if d and d["expires_on"] == old:
            new = _plus_year(old)
            con.execute("UPDATE documents SET expires_on=?, notes=? WHERE id=?", (new, f"{d['notes'] or ''}\nRenovado: vencía {old}, ahora {new} (al marcar la tarea de renovar como hecha).".strip(), d["id"]))
            db.audit(con, "document.renewed", "WRITE", f"document:{d['id']}", detail=f"{old} → {new}", actor="job")
            n += 1
    return n
