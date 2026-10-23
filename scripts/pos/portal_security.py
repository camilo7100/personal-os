"""Seguridad del portal: PIN (scrypt), sesiones firmadas, bloqueo por inactividad, alcances por origen y límite de intentos.
Nada de esto guarda secretos en archivos: el hash del PIN y la clave de firma viven en el Administrador de credenciales."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

import keyring
import typer

from . import db

app = typer.Typer(help="Seguridad del portal (PIN, sesiones, acceso remoto)", no_args_is_help=True)
SERVICE = "pos"
CONFIG = db.ROOT / "config" / "portal.json"
DEFAULTS = {
    "allowed_hosts": [],          # nombres por los que se accede desde fuera del PC (p. ej. el de Tailscale)
    "session_minutes": 525600,    # PC: la sesión dura 1 año rodante (se renueva al usar el portal); Windows ya protege el equipo
    "remote_session_minutes": 525600,  # dispositivos emparejados (celular, tableta, otro Windows): 1 año rodante; se revocan uno por uno
    "unlock_minutes": 43200,      # ventana de "desbloqueo" (solo se usa si pones rutas en "sensitive"; por defecto no hay ninguna)
    "pair_minutes": 10,           # vigencia del enlace/QR para emparejar un dispositivo (un solo uso)
    "sensitive": [],              # decisión del usuario (2026-10-02): el PIN se escribe una sola vez y abre todo; sin candado extra
    "remote_allowed": ["*"],      # "*" = todo se abre desde los dispositivos emparejados; para limitar, lista rutas (p. ej. ["/", "/tasks"])
    "remote_read_only": [],
}


def config() -> dict:
    try:
        return {**DEFAULTS, **json.loads(CONFIG.read_text(encoding="utf-8"))}
    except Exception:
        return dict(DEFAULTS)


def _kget(name: str) -> str | None:
    return keyring.get_password(SERVICE, name)


# ---------- PIN ----------
def pin_is_set() -> bool:
    return bool(_kget("portal-pin"))


def _hash_pin(pin: str, salt: bytes) -> str:
    return base64.b64encode(hashlib.scrypt(pin.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)).decode()


def set_pin(pin: str) -> None:
    if len(pin) < 6:
        raise ValueError("El PIN debe tener al menos 6 caracteres")
    salt = secrets.token_bytes(16)
    keyring.set_password(SERVICE, "portal-pin", f"{base64.b64encode(salt).decode()}${_hash_pin(pin, salt)}")
    rotate_secret()  # cerrar todas las sesiones anteriores


def reset_pin() -> None:
    """Recuperación: quita el PIN y cierra todas las sesiones. Solo se puede hacer con la sesión de Windows del dueño (la CLI local);
    el portal nunca ofrece esto en remoto. El PIN no cifra datos: al restablecerlo no se pierde nada."""
    try:
        keyring.delete_password(SERVICE, "portal-pin")
    except Exception:
        pass  # ya no había PIN
    rotate_secret()


def check_pin(pin: str) -> bool:
    stored = _kget("portal-pin")
    if not stored:
        return False
    salt_b64, digest = stored.split("$", 1)
    return hmac.compare_digest(_hash_pin(pin, base64.b64decode(salt_b64)), digest)


# ---------- sesiones firmadas ----------
def _secret() -> bytes:
    s = _kget("portal-secret")
    if not s:
        s = secrets.token_hex(32)
        keyring.set_password(SERVICE, "portal-secret", s)
    return s.encode()


def rotate_secret() -> None:
    keyring.set_password(SERVICE, "portal-secret", secrets.token_hex(32))


def sign(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"{body}.{hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()}"


def verify(token: str | None) -> dict | None:
    try:
        body, sig = (token or "").rsplit(".", 1)
        if not hmac.compare_digest(sig, hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()):
            return None
        return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except Exception:
        return None


def new_session(now: float | None = None) -> dict:
    now = now or time.time()
    return {"login": now, "last": now, "unlock": 0}


def session_seconds(local: bool = True) -> int:
    """Cuánto dura la sesión sin uso: más larga en el PC, más corta en el celular."""
    c = config()
    return int(c["session_minutes" if local else "remote_session_minutes"] * 60)


def session_valid(s: dict | None, now: float | None = None, local: bool = True) -> bool:
    now = now or time.time()
    return bool(s) and now - s.get("last", 0) <= session_seconds(local)


def unlocked(s: dict, now: float | None = None) -> bool:
    now = now or time.time()
    return now - s.get("unlock", 0) <= config()["unlock_minutes"] * 60


# ---------- límite de intentos ----------
_fails: dict[str, list[float]] = {}


def locked_seconds(key: str, now: float | None = None) -> int:
    """Tras 5 fallos en 15 min: bloqueo de 5 min por origen."""
    now = now or time.time()
    recent = [t for t in _fails.get(key, []) if now - t < 900]
    _fails[key] = recent
    if len(recent) >= 5:
        return max(0, int(300 - (now - recent[-1])))
    return 0


def register_fail(key: str, now: float | None = None) -> int:
    _fails.setdefault(key, []).append(now or time.time())
    return len(_fails[key])


def clear_fails(key: str) -> None:
    _fails.pop(key, None)


# ---------- alcance por origen ----------
def is_local_host(host: str) -> bool:
    return host.split(":")[0].lower() in ("127.0.0.1", "localhost")


def host_allowed(host: str) -> bool:
    return is_local_host(host) or host.split(":")[0].lower() in [h.lower() for h in config()["allowed_hosts"]]


def path_matches(path: str, prefixes: list[str]) -> bool:
    return any(path == p or (p != "/" and path.startswith(p + "/")) or (p != "/" and path.startswith(p + "?")) for p in prefixes)


def remote_permits(path: str, method: str) -> bool:
    c = config()
    if path.startswith("/static/") or path.startswith("/pair/") or path in ("/login", "/logout", "/sw.js"):
        return True
    if "*" not in c["remote_allowed"] and not path_matches(path, c["remote_allowed"]):
        return False
    return not (method != "GET" and path_matches(path, c["remote_read_only"]))


# ---------- CLI ----------
@app.command("set-pin")
def set_pin_cmd() -> None:
    """Crea o cambia el PIN del portal (lo escribes tú; no se guarda en ningún archivo)."""
    pin = typer.prompt("Nuevo PIN (mínimo 6 caracteres)", hide_input=True, confirmation_prompt=True)
    try:
        set_pin(pin)
    except ValueError as e:
        raise typer.BadParameter(str(e))
    with db.session() as con:
        db.audit(con, "portal.pin.set", "SENSITIVE_ACTION", "portal")
    typer.echo("PIN guardado. Todas las sesiones abiertas se cerraron.")


@app.command("reset-pin")
def reset_pin_cmd() -> None:
    """¿Olvidaste el PIN? Lo quita para que crees uno nuevo al abrir el portal. No se pierde ningún dato."""
    reset_pin()
    with db.session() as con:
        db.audit(con, "portal.pin.reset", "SENSITIVE_ACTION", "portal", detail="restablecido desde la sesión local de Windows")
    typer.echo("PIN eliminado y sesiones cerradas. Abre el portal: te pedirá crear uno nuevo. Tus datos siguen intactos.")


@app.command("lock")
def lock_cmd() -> None:
    """Cierra todas las sesiones abiertas del portal."""
    rotate_secret()
    typer.echo("Sesiones cerradas.")


@app.command("status")
def status_cmd() -> None:
    c = config()
    typer.echo(f"PIN: {'configurado' if pin_is_set() else 'SIN CONFIGURAR (crea uno con `pos portal set-pin`)'}")
    typer.echo(f"Acceso desde fuera del PC: {', '.join(c['allowed_hosts']) or 'desactivado (solo este equipo)'}")
    typer.echo(f"Sesión: {c['session_minutes']} min de inactividad · desbloqueo de lo sensible: {c['unlock_minutes']} min")


@app.command("allow-host")
def allow_host(host: str) -> None:
    """Permite el acceso remoto por este nombre (p. ej. el de Tailscale). Requiere PIN configurado."""
    if not pin_is_set():
        raise typer.BadParameter("Configura primero el PIN (`pos portal set-pin`)")
    try:  # el archivo guarda solo tus ajustes: lo demás sale de los valores por defecto (así las mejoras del sistema no quedan congeladas)
        mine = json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        mine = {}
    hosts = sorted(set(mine.get("allowed_hosts", [])) | {host.lower()})
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps({**mine, "allowed_hosts": hosts}, indent=2, ensure_ascii=False), encoding="utf-8")
    with db.session() as con:
        db.audit(con, "portal.allow-host", "SENSITIVE_ACTION", host)
    typer.echo(f"Permitido: {host}. Solo verás Hoy, Inbox, Tareas, Proyectos, Planes, Estudio, Conocimiento y Sistema; nunca Finanzas, Correo ni Empleo.")


# ---------- dispositivos emparejados ----------
def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_pair_token(now: float | None = None) -> str:
    """Enlace de un solo uso para emparejar un dispositivo (se guarda solo su huella, nunca el enlace)."""
    token = secrets.token_urlsafe(24)
    exp = (now or time.time()) + config()["pair_minutes"] * 60
    with db.session() as con:
        con.execute("DELETE FROM meta WHERE key LIKE 'pair:%' AND CAST(value AS REAL) < ?", (now or time.time(),))
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (f"pair:{_sha(token)}", str(exp)))
        db.audit(con, "portal.pair.create", "SENSITIVE_ACTION", "portal", detail=f"vence en {config()['pair_minutes']} min")
    return token


def consume_pair_token(token: str, now: float | None = None) -> bool:
    key = f"pair:{_sha(token or '')}"
    with db.session() as con:
        r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if not r:
            return False
        con.execute("DELETE FROM meta WHERE key=?", (key,))  # un solo uso
        return float(r["value"]) >= (now or time.time())


def register_device(label: str, user_agent: str = "") -> str:
    dev = secrets.token_hex(8)
    with db.session() as con:
        con.execute("INSERT INTO paired_devices(id, label, user_agent, last_seen) VALUES (?,?,?,datetime('now'))", (dev, label[:60], user_agent[:200]))
        db.audit(con, "portal.pair.done", "SENSITIVE_ACTION", f"device:{dev}", detail=label[:60])
    return dev


def device_ok(dev: str | None, touch: bool = True) -> bool:
    """¿Sigue autorizado el dispositivo de esta sesión? (sesiones sin dispositivo —las del PIN— siempre valen)."""
    if not dev:
        return True
    with db.session() as con:
        r = con.execute("SELECT revoked FROM paired_devices WHERE id=?", (dev,)).fetchone()
        if not r or r["revoked"]:
            return False
        if touch:
            con.execute("UPDATE paired_devices SET last_seen=datetime('now') WHERE id=? AND (last_seen IS NULL OR last_seen < datetime('now','-10 minutes'))", (dev,))
    return True


def guess_label(user_agent: str) -> str:
    u = user_agent.lower()
    kind = "Tableta Android" if ("android" in u and "mobile" not in u) else "Celular Android" if "android" in u else "iPhone/iPad" if ("iphone" in u or "ipad" in u) \
        else "Windows" if "windows" in u else "Mac" if "mac os" in u else "Linux" if "linux" in u else "Dispositivo"
    model = next((m for m in ("pixel", "samsung", "sm-", "xiaomi", "redmi", "motorola", "oneplus", "huawei") if m in u), "")
    return f"{kind}{' (' + model.upper() + ')' if model else ''}"


def devices() -> list[dict]:
    with db.session() as con:
        return [dict(r) for r in con.execute("SELECT id, label, paired_at, last_seen, revoked FROM paired_devices ORDER BY paired_at DESC")]


def revoke_device(dev: str) -> None:
    with db.session() as con:
        con.execute("UPDATE paired_devices SET revoked=1 WHERE id=?", (dev,))
        db.audit(con, "portal.pair.revoke", "SENSITIVE_ACTION", f"device:{dev}")


def new_session(now: float | None = None, dev: str | None = None) -> dict:  # noqa: F811 — misma función con dispositivo opcional
    now = now or time.time()
    s = {"login": now, "last": now, "unlock": now if dev else 0}
    if dev:
        s["dev"] = dev
    return s
