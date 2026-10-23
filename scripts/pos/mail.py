"""Correo multi-cuenta, solo lectura. Fuente: perfil local de Thunderbird 'pos-mail' (maildir o mbox).
Thunderbird gestiona el login OAuth de Gmail/Outlook; aquí nunca se tocan credenciales ni se envía nada.
Prefiltro determinista: solo lo que aporta valor llega a Claude (needs_claude=1)."""
import email
import email.policy
import json
import mailbox
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

import typer

from . import db

app = typer.Typer(help="Correo (solo lectura)", no_args_is_help=True)

PROFILES_DIR = db.APPDATA / "Thunderbird" / "Profiles"
PROFILE_SUFFIX = "pos-mail"
RULES_FILE = db.ROOT / "config" / "mail_rules.json"

RE_SECURITY_ALERT = re.compile(r"alerta de seguridad|security alert|actividad inusual|unusual (sign|activity)|nuevo inicio de sesión|new sign-in", re.I)
RE_SECURITY_CODE = re.compile(r"c[oó]digo (de )?(verificaci[oó]n|seguridad)|verification code|one-time|otp|password reset|restablec", re.I)
RE_FINANCE = re.compile(r"factura|invoice|recibo|receipt|pago|payment|vence|vencimiento|cobro|extracto|estado de cuenta|statement|amount due|suscripci[oó]n", re.I)
RE_CAREER = re.compile(r"entrevista|interview|vacante|postulaci[oó]n|application|oferta laboral|job offer|recruit|reclutad", re.I)
RE_STUDY = re.compile(r"universidad|maestr[ií]a|entrega|parcial|calificaci[oó]n|syllabus|campus|clase|tarea", re.I)
RE_PROMO = re.compile(r"descuento|oferta|% ?off|sale\b|promo|cyber|black friday|env[ií]o gratis|cup[oó]n", re.I)
RE_BULK_SENDER = re.compile(r"no-?reply|do-?not-?reply|newsletter|notifications?@|notice@|promotions?@|promo@|hello@|hola@|ofertas|comunicaciones@|"
                            r"transaction@|mailer|news@|info@|marketing|updates@|@(mails?|email|em|e|news|notice|info|hello|comunicaciones)\.", re.I)


def load_rules() -> dict:
    try:
        return json.loads(RULES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def classify(msg, rules: dict | None = None) -> tuple[str, int, str]:
    """(categoría, needs_claude, razón). Determinista y barato; Claude solo ve needs_claude=1."""
    rules = rules or {}
    sender = parseaddr(str(msg.get("From", "")))[1].lower()
    subject = str(msg.get("Subject", ""))
    if sender in [s.lower() for s in rules.get("vip_senders", [])]:
        return "human", 1, "remitente VIP"
    if sender in [s.lower() for s in rules.get("ignore_senders", [])] or \
            any(sender.endswith("@" + d.lower()) for d in rules.get("ignore_domains", [])):
        return "other", 0, "regla de ignorar"
    if RE_SECURITY_ALERT.search(subject):
        return "security", 1, "alerta de seguridad"
    if RE_SECURITY_CODE.search(subject):
        return "security", 0, "código/restablecimiento"
    bulk = bool(msg.get("List-Unsubscribe")) or str(msg.get("Precedence", "")).lower() in ("bulk", "list", "junk") \
        or bool(RE_BULK_SENDER.search(sender))
    if RE_FINANCE.search(subject) and not msg.get("List-Unsubscribe"):  # la publicidad trae «darse de baja»; las facturas reales casi nunca
        return "finance", 1, "posible cobro/pago"
    if RE_CAREER.search(subject) and not bulk:
        return "career", 1, "empleo"
    if msg.get_content_type() == "text/calendar" or subject.lower().startswith(("invitation:", "invitación:")):
        return "calendar", 1, "invitación"
    if RE_STUDY.search(subject) and not bulk:
        return "study", 1, "estudio"
    if bulk:
        return ("promo" if RE_PROMO.search(subject) else "newsletter"), 0, "envío masivo"
    return "human", 1, "persona/directo"


def _body(msg) -> str:
    part = msg.get_body(preferencelist=("plain", "html")) if hasattr(msg, "get_body") else None
    if part is None:
        return ""
    try:
        text = part.get_content()
    except Exception:
        return ""
    if part.get_content_type() == "text/html":
        text = re.sub(r"<(script|style).*?</\1>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


MAIL_PROFILE = db.DATA_DIR / "mail-profile"  # fuera de AppData: la app de Claude (MSIX) redirige AppData y Windows no vería el mismo perfil


def _find_profile() -> Path | None:
    """Perfil de Thunderbird del sistema: primero data\\mail-profile (visible para todos los procesos); si no, el de AppData."""
    if (MAIL_PROFILE / "prefs.js").exists():
        return MAIL_PROFILE
    if not PROFILES_DIR.exists():
        return None
    for p in PROFILES_DIR.iterdir():
        if p.is_dir() and p.name.endswith(PROFILE_SUFFIX):
            return p
    return None


def _account_map(profile: Path) -> dict[str, str]:
    """directorio de servidor → dirección de correo, leyendo prefs.js."""
    prefs = (profile / "prefs.js")
    if not prefs.exists():
        return {}
    txt = prefs.read_text(encoding="utf-8", errors="ignore")
    servers: dict[str, dict] = {}
    for m in re.finditer(r'user_pref\("mail\.server\.(server\d+)\.(directory-rel|directory|userName|hostname)", "([^"]*)"\);', txt):
        servers.setdefault(m.group(1), {})[m.group(2)] = m.group(3).replace("\\\\", "\\")
    out = {}
    for s in servers.values():
        user = s.get("userName", "?").replace("%40", "@")
        rel = s.get("directory-rel", "")
        folder = profile / rel.replace("[ProfD]", "") if rel.startswith("[ProfD]") else Path(s["directory"]) if s.get("directory") else None
        if folder and user != "nobody":  # «nobody» = Local Folders de Thunderbird, no es una cuenta
            out[str(folder.resolve()).lower()] = user
    return out


def _iter_folder_messages(folder: Path, skip: set[str] | None = None):
    """Devuelve (clave, mensaje) de una carpeta maildir (cur/new; Thunderbird a veces no crea `new`) o mbox.
    En maildir la clave es la ruta del archivo: los ya procesados (`skip`) ni se leen."""
    skip = skip or set()
    if folder.is_dir() and (folder / "cur").exists():
        for sub in ("cur", "new"):
            d = folder / sub
            if not d.is_dir():
                continue
            for f in d.iterdir():
                if f.is_file() and str(f) not in skip:
                    yield str(f), email.message_from_bytes(f.read_bytes(), policy=email.policy.default)
    elif folder.is_file() and folder.suffix == "":
        for i, m in enumerate(mailbox.mbox(folder, factory=lambda f: email.message_from_binary_file(f, policy=email.policy.default))):
            yield None, m


def _inbox_folders(account_dir: Path):
    for root, dirs, files in os.walk(account_dir):
        r = Path(root)
        if (r / "cur").exists():
            if r.name.upper() == "INBOX":
                yield r
            dirs[:] = []
            continue
        for f in files:
            if f.upper() == "INBOX" and "." not in f:
                yield r / f


def store_message(con, account: str, folder: str, msg, rules: dict) -> bool:
    mid = str(msg.get("Message-ID", "")).strip() or f"nomid-{hash((msg.get('Date'), msg.get('Subject'), msg.get('From')))}"
    if con.execute("SELECT 1 FROM emails WHERE account=? AND message_id=?", (account, mid)).fetchone():
        return False
    try:
        date = parsedate_to_datetime(str(msg.get("Date"))).astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        date = None
    name, addr = parseaddr(str(msg.get("From", "")))
    body = _body(msg)
    cat, needs, why = classify(msg, rules)
    con.execute(
        "INSERT INTO emails(account, message_id, folder, date, from_addr, from_name, subject, snippet, body_text, category, needs_claude, reason) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (account, mid, folder, date, addr.lower(), name, str(msg.get("Subject", ""))[:300], body[:300], body[:6000], cat, needs, why))
    return True


def ingest(since_days: int = 45) -> str:
    profile = _find_profile()
    if not profile:
        seen = sorted(x.name for x in PROFILES_DIR.iterdir()) if PROFILES_DIR.exists() else "carpeta inexistente"
        return f"sin perfil de Thunderbird 'pos-mail' (busqué en {PROFILES_DIR}; hay: {seen}; usuario: {Path.home().name})"
    accounts = _account_map(profile)
    rules = load_rules()
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    added = seen = 0
    for base in (profile / "ImapMail", profile / "Mail"):
        if not base.exists():
            continue
        for acc_dir in [d for d in base.iterdir() if d.is_dir()]:
            account = accounts.get(str(acc_dir.resolve()).lower(), acc_dir.name)
            with db.session() as con:
                for folder in _inbox_folders(acc_dir):
                    known = {r[0] for r in con.execute("SELECT path FROM mail_seen WHERE path LIKE ?", (str(folder) + "%",))}
                    for key, msg in _iter_folder_messages(folder, known):
                        seen += 1
                        if key:
                            con.execute("INSERT OR IGNORE INTO mail_seen(path) VALUES (?)", (key,))
                        try:
                            if parsedate_to_datetime(str(msg.get("Date"))).astimezone(timezone.utc) < cutoff:
                                continue
                        except Exception:
                            pass
                        added += store_message(con, account, folder.name, msg, rules)
                db.audit(con, "mail.ingest", "READ", account, detail=f"nuevos acumulados={added}", actor="job")
    return f"revisados={seen} nuevos={added}"


@app.command("ingest")
def ingest_cmd(since_days: int = 45) -> None:
    typer.echo(ingest(since_days))


@app.command()
def digest(limit: int = 30, as_json: bool = typer.Option(False, "--json")) -> None:
    """Resumen compacto para Claude: solo correos que pasaron el prefiltro y no han sido revisados."""
    with db.session() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT id, account, date, from_name, from_addr, subject, substr(snippet,1,200) AS snippet, category, reason "
            "FROM emails WHERE needs_claude=1 AND reviewed=0 ORDER BY date DESC LIMIT ?", (limit,))]
        stats = {r["category"]: r["n"] for r in con.execute(
            "SELECT category, COUNT(*) n FROM emails WHERE ingested_at >= datetime('now','-1 day') GROUP BY category")}
        db.audit(con, "mail.digest", "READ", detail=f"{len(rows)} correos")
    data = {"for_claude": rows, "ingested_last_24h_by_category": stats}
    if as_json:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return
    typer.echo(f"Filtrados últimas 24h: {stats}")
    for r in rows:
        typer.echo(f"#{r['id']} [{r['category']}] {r['account']} | {r['from_name'] or r['from_addr']} | {r['subject']}")
        typer.echo(f"     {r['snippet']}")


@app.command()
def review(ids: list[int]) -> None:
    """Marca correos como revisados por Claude/usuario."""
    with db.session() as con:
        con.executemany("UPDATE emails SET reviewed=1 WHERE id=?", [(i,) for i in ids])
        db.audit(con, "mail.review", "WRITE", detail=str(ids))
    typer.echo(f"{len(ids)} marcados")


@app.command()
def search(query: str, limit: int = 20, as_json: bool = typer.Option(False, "--json")) -> None:
    """Búsqueda de texto completo (FTS5) en todas las cuentas."""
    with db.session() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT e.id, e.account, e.date, e.from_addr, e.subject, e.category FROM emails_fts f "
            "JOIN emails e ON e.id=f.rowid WHERE emails_fts MATCH ? ORDER BY e.date DESC LIMIT ?", (query, limit))]
    if as_json:
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            typer.echo(f"#{r['id']} {r['date']} [{r['category']}] {r['account']} | {r['from_addr']} | {r['subject']}")
