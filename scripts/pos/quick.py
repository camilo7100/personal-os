"""Captura rápida en lenguaje natural (estilo Todoist, en español). Una línea → tarea completa:

    llamar al contador mañana 3pm ~30m p1 #Maestría @pago
    pagar la tarjeta cada mes el 18
    revisar informe viernes a las 10:30 por 1h

Reconoce fecha («mañana», «viernes», «el 15», «15 de octubre», «en 3 días», 2026-10-05), hora («3pm», «a las 15:30»), duración («~30m», «por 1h»),
prioridad («p1»…«p4», «!1»), proyecto («#Nombre» o #"Nombre largo"), etiquetas («@etiqueta»), repetición («cada lunes», «cada mes», «todos los días»)
y «algún día». Todo determinista (sin IA): sirve igual en el portal, Telegram y la terminal."""
import re
import unicodedata
from datetime import date, timedelta

WEEKDAYS = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6}
MONTHS = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9,
          "octubre": 10, "noviembre": 11, "diciembre": 12}


def fold(s: str) -> str:
    """Minúsculas sin tildes, carácter a carácter (conserva la longitud para poder recortar el texto original)."""
    return "".join(next((c for c in unicodedata.normalize("NFD", ch) if not unicodedata.combining(c)), ch) for ch in s).lower()


def _next_weekday(today: date, wd: int, strict: bool = True) -> date:
    n = (wd - today.weekday()) % 7
    return today + timedelta(days=n or (7 if strict else 0))


def _date_at(today: date, d: int, m: int, y: int | None = None) -> date | None:
    try:
        r = date(y or today.year, m, d)
    except ValueError:
        return None
    return r if (y or r >= today) else _date_at(today, d, m, today.year + 1)


class _Text:
    """Texto original + versión plegada alineada; permite quitar fragmentos y recordar qué se entendió."""

    def __init__(self, raw: str):
        self.raw, self.f, self.tokens = raw, fold(raw), []
        self.cut: list[tuple[int, int]] = []

    def find(self, pattern: str, flags: int = 0):
        for m in re.finditer(pattern, self.f, flags):
            if not any(a < m.end() and m.start() < b for a, b in self.cut):
                return m
        return None

    def take(self, m, kind: str, value) -> None:
        self.cut.append((m.start(), m.end()))
        self.tokens.append({"kind": kind, "text": self.raw[m.start():m.end()].strip(), "value": value})

    def rest(self) -> str:
        out, last = [], 0
        for a, b in sorted(self.cut):
            out.append(self.raw[last:a])
            last = max(last, b)
        out.append(self.raw[last:])
        t = re.sub(r"\s+", " ", "".join(out)).strip(" ,.;:-–—")
        t = re.sub(r"\b(para|el|la|de|a|en|por|al|del)\s*$", "", t, flags=re.I).strip(" ,.;:-–—")  # preposiciones que quedaron colgando
        return (t[:1].upper() + t[1:]) if t else ""


def parse_quick(text: str, projects: list[str] | None = None, today: date | None = None) -> dict:
    today = today or date.today()
    x = _Text(text)
    res = {"title": "", "due": None, "time": None, "est_minutes": None, "priority": None, "project": None, "tags": [], "recurrence": None, "someday": False}

    # etiquetas y proyecto
    while m := x.find(r'#"[^"]+"|#[^\s#@]+'):
        name = x.raw[m.start() + 1:m.end()].strip('"')
        hit = next((p for p in (projects or []) if fold(p) == fold(name)), None) or next((p for p in (projects or []) if fold(p).startswith(fold(name)) and len(name) >= 3), None)
        if hit and not res["project"]:
            res["project"] = hit
            x.take(m, "proyecto", hit)
        else:
            res["tags"].append(fold(name).replace(" ", "-"))
            x.take(m, "etiqueta", fold(name))
    while m := x.find(r"@[^\s#@]+"):
        res["tags"].append(x.f[m.start() + 1:m.end()])
        x.take(m, "etiqueta", x.f[m.start() + 1:m.end()])

    # prioridad
    if m := x.find(r"(?<![\w/])(?:p|!)([1-4])\b"):
        res["priority"] = int(m.group(1))
        x.take(m, "prioridad", res["priority"])

    # duración
    if m := x.find(r"(?:~|\bpor\s+|\bdurante\s+)\s*(\d+(?:[.,]\d+)?)\s*(horas?|hrs?|h|minutos?|mins?|m)?\b"):
        n, unit = float(m.group(1).replace(",", ".")), (m.group(2) or "m")
        res["est_minutes"] = int(round(n * 60)) if unit.startswith("h") else int(n)
        if m2 := re.match(r"\s*y\s*(\d+)\s*(?:min|m)\b", x.f[m.end():]):  # «1h y 30m»
            res["est_minutes"] += int(m2.group(1))
        x.take(m, "duración", res["est_minutes"])
    elif m := x.find(r"\b(\d+(?:[.,]\d+)?)\s*(horas?|hrs?|minutos?|mins?)\b"):
        n = float(m.group(1).replace(",", "."))
        res["est_minutes"] = int(round(n * 60)) if m.group(2).startswith("h") else int(n)
        x.take(m, "duración", res["est_minutes"])

    # repetición
    rec = None
    if m := x.find(r"\b(?:todos los dias|cada dia|diari[oa]mente|diario)\b"):
        rec = "daily"
    elif m := x.find(r"\bcada\s+(?:2|dos)\s+semanas\b|\bquincenal(?:mente)?\b"):
        rec = "biweekly"
    elif m := x.find(r"\bcada\s+(?:3|tres)\s+meses\b|\btrimestral(?:mente)?\b"):
        rec = "quarterly"
    elif m := x.find(r"\bcada\s+(" + "|".join(WEEKDAYS) + r")\b"):
        rec = "weekly"
        res["due"] = _next_weekday(today, WEEKDAYS[m.group(1)], strict=False).isoformat()
    elif m := x.find(r"\bcada\s+semana\b|\bsemanal(?:mente)?\b|\btodas las semanas\b"):
        rec = "weekly"
    elif m := x.find(r"\bcada\s+mes\b|\bmensual(?:mente)?\b|\btodos los meses\b"):
        rec = "monthly"
    elif m := x.find(r"\bcada\s+(?:ano|year)\b|\banual(?:mente)?\b|\btodos los anos\b"):
        rec = "yearly"
    if rec:
        res["recurrence"] = rec
        x.take(m, "repetición", rec)

    # hora
    am_pm = r"(?:\s*(?:de la\s+)?(am|pm|manana|tarde|noche))?"
    tm = x.find(r"\b(?:a las?\s+)?(\d{1,2}):(\d{2})" + am_pm + r"\b") or x.find(r"\ba las?\s+(\d{1,2})()" + am_pm + r"\b") or x.find(r"\b(\d{1,2})()\s*(am|pm)\b")
    if tm:
        h, mi, suf = int(tm.group(1)), int(tm.group(2) or 0), (tm.lastindex and tm.group(tm.lastindex) in ("am", "pm", "manana", "tarde", "noche") and tm.group(tm.lastindex)) or ""
        if suf in ("pm", "tarde", "noche") and h < 12:
            h += 12
        if suf in ("am", "manana") and h == 12:
            h = 0
        if 0 <= h < 24 and 0 <= mi < 60:
            res["time"] = f"{h:02d}:{mi:02d}"
            x.take(tm, "hora", res["time"])

    # fecha
    d = None
    if m := x.find(r"\b(\d{4})-(\d{2})-(\d{2})\b"):
        d = _date_at(today, int(m.group(3)), int(m.group(2)), int(m.group(1)))
    elif m := x.find(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b"):
        y = int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0) if m.group(3) else None
        d = _date_at(today, int(m.group(1)), int(m.group(2)), y)
    elif m := x.find(r"\bpasado manana\b"):
        d = today + timedelta(days=2)
    elif m := x.find(r"(?<!de la )\bmanana\b"):
        d = today + timedelta(days=1)
    elif m := x.find(r"\b(?:hoy|esta noche|esta tarde|esta manana)\b"):
        d = today
    elif m := x.find(r"\ben\s+(\d{1,3})\s+(dias?|semanas?|meses|mes)\b"):
        n, u = int(m.group(1)), m.group(2)
        d = today + timedelta(days=n * (1 if u.startswith("dia") else 7 if u.startswith("sem") else 30))
    elif m := x.find(r"\b(?:la\s+)?proxima semana\b"):
        d = today + timedelta(days=7 - today.weekday())
    elif m := x.find(r"\b(?:el\s+)?(?:proximo\s+|este\s+)?(" + "|".join(WEEKDAYS) + r")\b"):
        d = _next_weekday(today, WEEKDAYS[m.group(1)])
    elif m := x.find(r"\b(?:el\s+)?(\d{1,2})\s+de\s+(" + "|".join(MONTHS) + r")(?:\s+de\s+(\d{4}))?\b"):
        d = _date_at(today, int(m.group(1)), MONTHS[m.group(2)], int(m.group(3)) if m.group(3) else None)
    elif m := x.find(r"\bel\s+(\d{1,2})\b(?!\s*(?:de|:))"):
        day = int(m.group(1))
        d = next((r for r in (_date_at(today, day, today.month), _date_at(today, day, today.month % 12 + 1, today.year + (today.month == 12))) if r and r >= today), None)
    if d:
        res["due"] = d.isoformat()
        x.take(m, "fecha", res["due"])

    if m := x.find(r"\balgun dia\b|\bsomeday\b|\bcuando pueda\b"):
        res["someday"] = True
        x.take(m, "algún día", True)

    if res["time"] and not res["due"]:
        res["due"] = today.isoformat()
    if res["recurrence"] and not res["due"]:
        res["due"] = today.isoformat()
    res["tags"] = sorted(set(res["tags"]))
    res["title"] = x.rest() or text.strip()
    res["tokens"] = sorted(x.tokens, key=lambda t: text.find(t["text"]) if t["text"] in text else 0)
    return res


def describe(r: dict) -> str:
    """Resumen legible de lo entendido (para mostrar al instante bajo la caja de captura)."""
    from .tgfmt import short
    bits = []
    if r["due"]:
        bits.append(f"📅 {short(r['due'])}" + (f" {r['time']}" if r["time"] else ""))
    if r["est_minutes"]:
        bits.append(f"⏱ {r['est_minutes']} min")
    if r["priority"]:
        bits.append(f"🚩 P{r['priority']}")
    if r["project"]:
        bits.append(f"📁 {r['project']}")
    bits += [f"#{t}" for t in r["tags"]]
    if r["recurrence"]:
        bits.append("↻ " + {"daily": "diaria", "weekly": "semanal", "biweekly": "quincenal", "monthly": "mensual", "quarterly": "trimestral", "yearly": "anual"}[r["recurrence"]])
    if r["someday"]:
        bits.append("🌙 algún día")
    return " · ".join(bits)
