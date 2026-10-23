"""Clasificación del inbox (N2, determinista): tarea / nota / descarte + fecha, prioridad y proyecto, en español.
Instantáneo y sin IA: un LLM local (Ollama) tardaba ~40 s por frase en este equipo, así que aquí un script basta.
Deja una sugerencia en cada ítem; el usuario (portal) o Claude decide."""
import json
import re
import unicodedata
from datetime import date, timedelta

from . import db

VERBS = (
    "comprar llamar pagar enviar mandar revisar agendar reservar renovar terminar entregar hacer preparar escribir leer estudiar "
    "cancelar cotizar recoger llevar arreglar instalar actualizar declarar cobrar facturar devolver buscar programar imprimir firmar "
    "tramitar sacar pedir confirmar contactar resolver cerrar publicar subir descargar probar organizar limpiar cambiar recordar "
    "investigar responder escribirle hablar visitar agregar anadir crear configurar cargar ajustar completar actualizarle").split()
NOTE_PREFIX = re.compile(r"^\s*(idea|nota|tip|dato|recuerdo|pensamiento|referencia)\s*[:\-–]\s*", re.I)
DAYS = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6}
MONTHS = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
          "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def parse_due(text: str, today: date | None = None) -> str | None:
    """Entiende: hoy, mañana, pasado mañana, lunes…domingo, el 15, 15 de octubre, en 3 días, 2026-10-05, 05/10."""
    today, t = today or date.today(), _norm(text)
    if m := re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t):
        return m.group(0)
    if m := re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t):
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) + (2000 if m.group(3) and len(m.group(3)) == 2 else 0) if m.group(3) else today.year
        try:
            r = date(y, mo, d)
            return (r if r >= today or m.group(3) else date(y + 1, mo, d)).isoformat()
        except ValueError:
            pass
    if re.search(r"\bpasado manana\b", t):
        return (today + timedelta(days=2)).isoformat()
    if re.search(r"\bmanana\b", t):
        return (today + timedelta(days=1)).isoformat()
    if re.search(r"\bhoy\b|\besta noche\b", t):
        return today.isoformat()
    if m := re.search(r"\ben (\d{1,3}) dias?\b", t):
        return (today + timedelta(days=int(m.group(1)))).isoformat()
    if re.search(r"\b(la )?proxima semana\b", t):
        return (today + timedelta(days=7 - today.weekday())).isoformat()
    for name, wd in DAYS.items():
        if re.search(rf"\b{name}\b", t):
            return (today + timedelta(days=(wd - today.weekday()) % 7 or 7)).isoformat()
    if m := re.search(r"\b(\d{1,2}) de (" + "|".join(MONTHS) + r")\b", t):
        try:
            r = date(today.year, MONTHS[m.group(2)], int(m.group(1)))
            return (r if r >= today else date(today.year + 1, r.month, r.day)).isoformat()
        except ValueError:
            pass
    if m := re.search(r"\bel (\d{1,2})\b(?! de)", t):
        d = int(m.group(1))
        for add in (0, 1):  # este mes o el siguiente
            y, mo = today.year + (today.month + add - 1) // 12, (today.month + add - 1) % 12 + 1
            try:
                r = date(y, mo, d)
                if r >= today:
                    return r.isoformat()
            except ValueError:
                continue
    return None


def classify(text: str, projects: list[str], today: date | None = None) -> dict:
    raw = text.strip()
    n = _norm(raw)
    if len(n) < 3 or n in ("test", "prueba", "asdf", "xxx"):
        return {"kind": "discard", "title": raw, "due": None, "project": None, "priority": 3}
    due = parse_due(raw, today)
    words = re.findall(r"[a-z]+", n)
    first = words[0] if words else ""
    is_note = bool(NOTE_PREFIX.match(raw)) or bool(re.match(r"https?://", raw))
    is_task = not is_note and (first in VERBS or (first.endswith(("ar", "er", "ir")) and len(first) > 4 and due is not None)
                              or due is not None or bool(re.search(r"\b(hay que|toca|debo|tengo que|recordarme|pendiente)\b", n)))
    title = NOTE_PREFIX.sub("", raw).strip()
    title = (title[0].upper() + title[1:])[:200] if title else raw
    prio = 1 if re.search(r"\b(urgente|ya mismo|cuanto antes|hoy mismo)\b", n) else 2 if re.search(r"\b(importante|prioridad)\b", n) else 3
    project = None
    for p in projects:  # coincide si comparte una palabra significativa (≥4 letras) con el nombre del proyecto
        pw = {w for w in re.findall(r"[a-z]+", _norm(p)) if len(w) >= 4}
        if pw & set(words):
            project = p
            break
    return {"kind": "task" if is_task else "note", "title": title, "due": due, "project": project, "priority": prio}


def run(limit: int = 100) -> str:
    """Job: sugiere qué hacer con cada ítem nuevo del inbox que aún no tiene sugerencia."""
    with db.session() as con:
        items = con.execute("SELECT id, text FROM inbox WHERE status='new' AND suggestion IS NULL ORDER BY id LIMIT ?", (limit,)).fetchall()
        projects = [r["name"] for r in con.execute("SELECT name FROM projects WHERE status!='done'")]
        for it in items:
            con.execute("UPDATE inbox SET suggestion=? WHERE id=?", (json.dumps(classify(it["text"], projects), ensure_ascii=False), it["id"]))
    return f"{len(items)} sugerencias" if items else "nada que clasificar"
