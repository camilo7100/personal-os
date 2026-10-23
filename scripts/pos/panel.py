"""Panel personal fuera del PC: una copia de SOLO LECTURA del portal, cifrada, publicada en un hosting estático del usuario (GitHub Pages).

Cómo funciona:
  · `pos panel build`   renderiza las páginas del portal (con tu sesión local), las comprime y las cifra con AES-256-GCM → `data\\panel-site\\data.bin`
                        junto con el visor (portal\\panel_site: HTML + JS sin dependencias).
  · `pos panel publish` sube esa carpeta al repositorio público `<usuario>/panel-personal` (una sola confirmación, historial reescrito: no se acumulan copias).
                        En el repositorio solo hay el visor y texto cifrado: sin la llave nadie lee nada.
  · `pos panel link`    imprime el enlace de emparejamiento: la llave viaja en el #fragmento (el navegador nunca la envía al servidor) y queda guardada
                        en ese equipo. Cada enlace generado se avisa por Telegram. Equipo perdido → `pos panel key --force` (los demás se vuelven a emparejar).
  · Job `panel`         cada 3 horas y al encender el PC: reconstruye y publica si algo cambió.
La llave de cifrado (`panel-key`) vive en el Administrador de credenciales (servicio `pos`); nada secreto en archivos ni en el repositorio."""
import base64
import gzip
import hashlib
import html
import importlib.util
import json
import re
import secrets
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import keyring
import typer

from . import db

app = typer.Typer(help="Panel personal fuera del PC: copia cifrada de solo lectura", no_args_is_help=True)
SERVICE = "pos"
KEY_NAME = "panel-key"
CONFIG = db.ROOT / "config" / "panel.json"
SITE = db.DATA_DIR / "panel-site"
VIEWER = db.ROOT / "portal" / "panel_site"
DEFAULTS = {"repo": "<usuario>/panel-personal", "url": "https://<usuario>.github.io/panel-personal/", "months": 12}
BASE_PAGES = ["/", "/inbox", "/tasks", "/tasks?view=tablero", "/tasks?view=matriz", "/tasks?view=todas", "/calendar", "/calendar?view=week", "/calendar?view=agenda",
              "/review", "/projects", "/goals", "/plans", "/money", "/career", "/life", "/study", "/knowledge", "/mail", "/manual", "/system"]


def config() -> dict:
    try:
        return {**DEFAULTS, **json.loads(CONFIG.read_text(encoding="utf-8"))}
    except Exception:
        return dict(DEFAULTS)


# ---------- llave ----------
def key_bytes() -> bytes | None:
    k = keyring.get_password(SERVICE, KEY_NAME)
    return base64.urlsafe_b64decode(k + "=" * (-len(k) % 4)) if k else None


def key_text() -> str | None:
    return keyring.get_password(SERVICE, KEY_NAME)


def new_key() -> str:
    """Genera la llave (256 bits). Los equipos ya emparejados dejan de servir hasta que se vuelvan a emparejar."""
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    keyring.set_password(SERVICE, KEY_NAME, key)
    with db.session() as con:
        db.audit(con, "panel.key.new", "SENSITIVE_ACTION", "panel", detail="llave de cifrado del panel generada")
        con.execute("DELETE FROM meta WHERE key='panel:last_hash'")  # fuerza una publicación nueva con la llave nueva
    return key


def pair_link() -> str:
    """Enlace de emparejamiento (la llave va en el #fragmento). Avisa por Telegram que se generó."""
    k = key_text()
    if not k:
        raise ValueError("Primero crea la llave: `pos panel key`")
    with db.session() as con:
        db.audit(con, "panel.pair.link", "SENSITIVE_ACTION", "panel", detail="enlace de emparejamiento generado")
    try:
        from . import telegram
        telegram.send("🔑 Se generó un enlace de emparejamiento del panel. Quien lo abra queda con acceso de lectura a TODO tu panel. Si no fuiste tú, renueva la llave: `pos panel key --force`.")
    except Exception:
        pass
    return f"{config()['url'].rstrip('/')}/#k={k}"


# ---------- construir ----------
def _load_portal():
    spec = importlib.util.spec_from_file_location("pos_portal_app", db.ROOT / "portal" / "app.py")
    sys.path.insert(0, str(db.ROOT / "portal"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _picker(ms: list[str], ys: list[str]) -> str:
    """El selector de mes/año de Finanzas necesita scripts: en la copia se vuelve una fila de enlaces."""
    chip = 'style="display:inline-block;margin:2px 4px 2px 0;padding:3px 10px;border:1px solid #64748b;border-radius:999px;text-decoration:none"'
    yrs = "".join(f'<a {chip} href="/money/year?year={y}">Año {y}</a>' for y in ys)
    mos = "".join(f'<a {chip} href="/money?month={m}">{m}</a>' for m in ms)
    return f'<div style="margin-bottom:14px">{yrs}<br>{mos}</div>'


def _static_page(raw: str, css: str, picker: str = "") -> str:
    """HTML del portal → página autónoma sin scripts: CSS embebido, enlaces internos como #/ruta (dentro del visor) y nada que dependa del servidor."""
    raw = re.sub(r"<script\b.*?</script>", "", raw, flags=re.S | re.I)
    raw = re.sub(r'<form[^>]*action="/money/go".*?</form>', lambda m: picker, raw, flags=re.S)
    raw = re.sub(r"<link\b[^>]*rel=\"(?:manifest|icon|apple-touch-icon)\"[^>]*>", "", raw, flags=re.I)
    raw = re.sub(r"<link\b[^>]*stylesheet[^>]*>", lambda m: f"<style>{css}</style>", raw, count=1, flags=re.I)

    def link(m: re.Match) -> str:
        href = html.unescape(m.group(2))
        if re.match(r"^/(logout|login|unlock|devices|static)", href):
            return f'<a {m.group(1)}href="#/" target="_top"'
        target = href.split("#")[0]
        return f'<a {m.group(1)}href="#{html.escape(target if target else "/")}" target="_top"'
    raw = re.sub(r"<a\s+([^>]*?)href=\"(/[^\"]*)\"", link, raw)
    return raw


def build(months: int | None = None) -> dict:
    """Renderiza y cifra. Devuelve {pages, bytes, hash}."""
    key = key_bytes()
    if not key:
        raise ValueError("Primero crea la llave: `pos panel key`")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from fastapi.testclient import TestClient
    portal = _load_portal()
    from . import portal_security as sec
    portal.scheduler_info = lambda: {}
    client = TestClient(portal.app, base_url="http://127.0.0.1:8765", follow_redirects=False)
    s = sec.new_session()
    s["unlock"] = s["last"]
    client.cookies.set("pos_session", sec.sign(s))
    css = (db.ROOT / "portal" / "static" / "style.css").read_text(encoding="utf-8")

    pages = list(BASE_PAGES)
    with db.session() as con:
        ms = [r[0] for r in con.execute("SELECT DISTINCT substr(date,1,7) FROM transactions ORDER BY 1 DESC LIMIT ?", (months or config()["months"],))]
        ys = [r[0] for r in con.execute("SELECT DISTINCT substr(date,1,4) FROM transactions ORDER BY 1 DESC")]
        pl = [r[0] for r in con.execute("SELECT id FROM plans")]
    pages += [f"/money?month={m}" for m in ms] + [f"/money/year?year={y}" for y in ys] + [f"/plans/{i}" for i in pl]
    raw: dict[str, str] = {}
    for p in pages:
        r = client.get(p)
        if r.status_code == 200:
            raw[p] = r.text
    out = {p: _static_page(t, css, _picker(ms, ys)) for p, t in raw.items()}
    stable = json.dumps(out, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(stable.encode()).hexdigest()
    payload = json.dumps({"generated": datetime.now().astimezone().isoformat(timespec="seconds"), "pages": out}, ensure_ascii=False).encode()
    nonce = secrets.token_bytes(12)
    blob = nonce + AESGCM(key).encrypt(nonce, gzip.compress(payload, 9), None)
    SITE.mkdir(parents=True, exist_ok=True)
    for f in VIEWER.iterdir():
        shutil.copy2(f, SITE / f.name)
    static = db.ROOT / "portal" / "static"
    for n in ("favicon.ico", "apple-touch-icon.png", "icon-192.png", "icon-512.png"):
        shutil.copy2(static / n, SITE / n)
    (SITE / "data.bin").write_bytes(blob)
    (SITE / ".nojekyll").write_text("")
    return {"pages": len(out), "bytes": len(blob), "hash": digest}


# ---------- publicar ----------
def _run(args: list[str], cwd: Path | None = None, check: bool = True) -> str:
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=300)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])}: {(r.stderr or r.stdout).strip()[:300]}")
    return (r.stdout or "").strip()


def publish() -> str:
    """Sube data\\panel-site al repositorio de GitHub Pages (crea el repositorio y activa Pages la primera vez)."""
    cfg = config()
    repo = cfg["repo"]
    exists = subprocess.run(["gh", "repo", "view", repo], capture_output=True, text=True).returncode == 0
    if not exists:
        _run(["gh", "repo", "create", repo, "--public", "--description", "Visor cifrado"])
    if not (SITE / ".git").exists():
        _run(["git", "init", "-b", "main"], SITE)
        _run(["git", "remote", "add", "origin", f"https://github.com/{repo}.git"], SITE)
    _run(["git", "config", "user.name", "panel"], SITE)
    _run(["git", "config", "user.email", "panel@users.noreply.github.com"], SITE)
    _run(["git", "checkout", "--orphan", "tmp"], SITE, check=False)
    _run(["git", "add", "-A"], SITE)
    _run(["git", "commit", "-m", f"copia {datetime.now():%Y-%m-%d %H:%M}", "--allow-empty"], SITE)
    _run(["git", "push", "--force", "origin", "tmp:main"], SITE)
    _run(["git", "branch", "-M", "main"], SITE, check=False)
    pages = subprocess.run(["gh", "api", f"repos/{repo}/pages"], capture_output=True, text=True)
    if pages.returncode != 0:
        _run(["gh", "api", "-X", "POST", f"repos/{repo}/pages", "-f", "source[branch]=main", "-f", "source[path]=/"])
    return cfg["url"]


def run_job() -> str:
    if not key_bytes():
        return "sin llave: no se publica"
    b = build()
    with db.session() as con:
        last = con.execute("SELECT value FROM meta WHERE key='panel:last_hash'").fetchone()
    if last and last["value"] == b["hash"]:
        return f"sin cambios ({b['pages']} páginas)"
    url = publish()
    with db.session() as con:
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('panel:last_hash', ?)", (b["hash"],))
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('panel:last_publish', ?)", (datetime.now().isoformat(timespec="seconds"),))
    return f"publicado: {b['pages']} páginas, {b['bytes'] // 1024} KB → {url}"


# ---------- CLI ----------
@app.command("status")
def status_cmd() -> None:
    """Estado (no muestra ningún secreto)."""
    cfg = config()
    typer.echo(f"Llave de cifrado: {'sí' if key_bytes() else 'no (usa `pos panel key`)'}")
    typer.echo(f"Destino: {cfg['url']}  (repositorio {cfg['repo']})")
    with db.session() as con:
        last = con.execute("SELECT value FROM meta WHERE key='panel:last_publish'").fetchone()
    typer.echo(f"Última publicación: {last['value'] if last else 'nunca'}")


@app.command("key")
def key_cmd(force: bool = typer.Option(False, "--force", help="Reemplaza la llave (los equipos ya emparejados dejan de servir)")) -> None:
    """Crea la llave de cifrado del panel."""
    if key_bytes() and not force:
        typer.echo("Ya existe. Con --force se reemplaza (hay que volver a emparejar los equipos).")
        raise typer.Exit(1)
    new_key()
    typer.echo("Llave creada y guardada en el Administrador de credenciales.")


@app.command("build")
def build_cmd() -> None:
    """Genera la copia cifrada en data\\panel-site (sin publicar)."""
    b = build()
    typer.echo(f"{b['pages']} páginas · {b['bytes'] // 1024} KB cifrados · {SITE}")


@app.command("publish")
def publish_cmd() -> None:
    """Construye y publica ahora."""
    typer.echo(run_job())


@app.command("link")
def link_cmd() -> None:
    """Enlace de emparejamiento para un equipo nuevo (se avisa por Telegram)."""
    typer.echo(pair_link())
