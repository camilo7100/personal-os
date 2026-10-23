import sys
import time
from pathlib import Path

import keyring
import pytest
from fastapi.testclient import TestClient
from keyring.backend import KeyringBackend

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "portal"))


class MemKeyring(KeyringBackend):
    priority = 99

    def __init__(self):
        self.d = {}

    def set_password(self, service, username, password):
        self.d[(service, username)] = password

    def get_password(self, service, username):
        return self.d.get((service, username))

    def delete_password(self, service, username):
        self.d.pop((service, username), None)


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    kr = MemKeyring()
    monkeypatch.setattr(keyring, "get_password", kr.get_password)
    monkeypatch.setattr(keyring, "set_password", kr.set_password)
    monkeypatch.setattr(keyring, "delete_password", kr.delete_password)
    monkeypatch.setenv("POS_DB", str(tmp_path / "pos.db"))
    monkeypatch.setenv("POS_HOME", str(tmp_path))
    for m in [m for m in sys.modules if m in ("pos", "app") or m.startswith("pos.")]:
        del sys.modules[m]
    from pos import db, media, notes
    db.init_db()
    monkeypatch.setattr(media, "MEDIA_HOME", tmp_path / "media")
    monkeypatch.setattr(notes, "VAULT", tmp_path / "kb")
    import app as portal
    from pos import portal_security as sec
    monkeypatch.setattr(portal, "scheduler_info", lambda: {})
    mk = lambda host="127.0.0.1:8765": TestClient(portal.app, base_url=f"{'http' if host.startswith('127') else 'https'}://{host}", follow_redirects=False)
    return mk, sec, tmp_path


def test_without_pin_local_works_with_banner_and_remote_is_blocked(app_env):
    mk, sec, _ = app_env
    sec.CONFIG.parent.mkdir(exist_ok=True)
    local = mk()
    r = local.get("/")
    assert r.status_code == 200 and "no tiene PIN" in r.text
    assert mk("evil.example.com").get("/").status_code == 403  # host no autorizado


def test_setup_login_lock_and_headers(app_env):
    mk, sec, _ = app_env
    c = mk()
    assert c.post("/setup", data={"pin": "123", "pin2": "123"}).status_code == 400  # muy corto
    r = c.post("/setup", data={"pin": "clave-larga-1", "pin2": "clave-larga-1"})
    assert r.status_code == 303 and "pos_session" in r.cookies
    assert c.get("/").status_code == 200
    assert "HttpOnly" in r.headers["set-cookie"] and "SameSite=strict" in r.headers["set-cookie"].replace("samesite", "SameSite")
    page = c.get("/")
    assert "script-src 'self'" in page.headers["content-security-policy"] and page.headers["x-frame-options"] == "DENY"
    fresh = mk()  # otro navegador sin sesión
    assert fresh.get("/tasks").status_code == 303 and "/login" in fresh.get("/tasks").headers["location"]
    assert fresh.get("/setup").status_code == 303  # ya hay PIN: no se puede recrear
    bad = fresh.post("/login", data={"pin": "incorrecto", "next": "/"})
    assert bad.status_code == 401
    ok = fresh.post("/login", data={"pin": "clave-larga-1", "next": "/tasks"})
    assert ok.status_code == 303 and ok.headers["location"] == "/tasks"


def test_five_failures_lock_out_even_correct_pin(app_env):
    mk, sec, _ = app_env
    sec.set_pin("clave-larga-1")
    c = mk()
    for _ in range(5):
        assert c.post("/login", data={"pin": "mal", "next": "/"}).status_code == 401
    assert c.post("/login", data={"pin": "clave-larga-1", "next": "/"}).status_code == 429


def test_pin_once_opens_everything_and_idle_year_expires(app_env, monkeypatch):
    mk, sec, _ = app_env
    sec.set_pin("clave-larga-1")
    real, off = time.time, {"v": 0}
    monkeypatch.setattr(time, "time", lambda: real() + off["v"])  # avanzar el reloj sin esperar
    c = mk()
    c.post("/login", data={"pin": "clave-larga-1", "next": "/"})
    assert c.get("/money").status_code == 200
    off["v"] = 200 * 24 * 3600  # meses después: sigue entrando solo, sin ningún candado extra
    assert c.get("/money").status_code == 200 and c.get("/mail").status_code == 200
    off["v"] += 400 * 24 * 3600  # más de un año sin usarlo: hay que entrar de nuevo
    assert c.get("/tasks").status_code == 303


def test_sessions_last_a_year_everywhere(app_env):
    _, sec, _ = app_env
    assert sec.session_seconds(local=True) == 365 * 24 * 3600 == sec.session_seconds(local=False)
    s = sec.new_session(1000.0)
    assert sec.session_valid(s, now=1000.0 + 300 * 24 * 3600, local=False)
    assert not sec.session_valid(s, now=1000.0 + 400 * 24 * 3600, local=False)


def test_pairing_a_device_without_typing_the_pin_and_revoking_it(app_env, monkeypatch):
    mk, sec, _ = app_env
    sec.set_pin("clave-larga-1")
    (app_env[2] / "config").mkdir(exist_ok=True)
    (app_env[2] / "config" / "portal.json").write_text('{"allowed_hosts": ["pc.ts.net"]}', encoding="utf-8")
    pc = mk()
    pc.post("/login", data={"pin": "clave-larga-1", "next": "/"})
    r = pc.post("/devices/new")
    token = r.headers["location"].split("token=")[1]
    page = pc.get(f"/devices?token={token}").text
    assert f"https://pc.ts.net/pair/{token}" in page and "<svg" in page                      # enlace + QR
    phone = mk("pc.ts.net")
    assert phone.get("/tasks").status_code == 303                                           # sin emparejar no entra
    ok = phone.get(f"/pair/{token}", headers={"user-agent": "Mozilla/5.0 (Linux; Android 15; Pixel 8 Pro) Mobile"})
    assert ok.status_code == 303 and "pos_session" in ok.headers["set-cookie"] and "Max-Age=31536000" in ok.headers["set-cookie"]
    assert phone.get("/tasks").status_code == 200 and phone.get("/calendar").status_code == 200  # entra sin PIN
    assert phone.get("/money").status_code == 200 and phone.get("/devices").status_code == 200    # el celular emparejado abre todo con el mismo emparejamiento
    assert mk("pc.ts.net").get(f"/pair/{token}").status_code == 410                         # el enlace es de un solo uso
    dev = sec.devices()[0]
    assert dev["label"].startswith("Celular Android") and "PIXEL" in dev["label"]
    pc.post(f"/devices/{dev['id']}/revoke")
    assert phone.get("/tasks").status_code == 303                                           # desconectado: sale al instante


def test_pair_link_expires_and_only_hash_is_stored(app_env, monkeypatch):
    _, sec, _ = app_env
    from pos import db
    t0 = time.time()
    tok = sec.create_pair_token(t0)
    with db.session() as con:
        keys = [r["key"] for r in con.execute("SELECT key FROM meta WHERE key LIKE 'pair:%'")]
    assert keys and tok not in keys[0]                                                       # nunca se guarda el enlace en claro
    assert not sec.consume_pair_token(tok, t0 + 11 * 60)                                     # venció a los 10 minutos
    assert not sec.consume_pair_token("inventado")


def test_logout_really_closes_session(app_env):
    mk, sec, _ = app_env
    sec.set_pin("clave-larga-1")
    c = mk()
    c.post("/login", data={"pin": "clave-larga-1", "next": "/"})
    assert c.get("/tasks").status_code == 200
    c.get("/logout")
    assert c.get("/tasks").status_code == 303


def test_forged_and_rotated_sessions_rejected(app_env):
    mk, sec, _ = app_env
    sec.set_pin("clave-larga-1")
    c = mk()
    c.cookies.set("pos_session", "eyJsYXN0IjoxfQ.firmafalsa")
    assert c.get("/tasks").status_code == 303
    c2 = mk()
    c2.post("/login", data={"pin": "clave-larga-1", "next": "/"})
    assert c2.get("/tasks").status_code == 200
    sec.rotate_secret()  # `pos portal lock`
    assert c2.get("/tasks").status_code == 303


def test_remote_devices_open_everything_with_the_same_pin(app_env):
    mk, sec, tmp = app_env
    sec.set_pin("clave-larga-1")
    sec.CONFIG.parent.mkdir(exist_ok=True)
    sec.CONFIG.write_text('{"allowed_hosts": ["pc.tail1234.ts.net"]}')
    r = mk("pc.tail1234.ts.net")
    assert r.get("/login").status_code == 200
    assert r.get("/money").status_code == 303  # sin sesión todavía: pide entrar
    r.post("/login", data={"pin": "clave-larga-1", "next": "/"})
    for page in ("/tasks", "/money", "/mail", "/career", "/life"):
        assert r.get(page).status_code == 200, page
    home = r.get("/").text
    assert 'href="/money"' in home and 'href="/mail"' in home
    assert mk("otro.ts.net").get("/").status_code == 403  # un nombre que no autorizaste sigue bloqueado


def test_forgotten_pin_can_be_reset_locally_without_losing_data(app_env):
    mk, sec, _ = app_env
    from pos import db, services
    with db.session() as con:
        services.add_task(con, "No debe perderse")
    sec.set_pin("clave-larga-1")
    c = mk()
    c.post("/login", data={"pin": "clave-larga-1", "next": "/"})
    assert c.get("/tasks").status_code == 200
    from typer.testing import CliRunner
    from pos.cli import app as cli
    r = CliRunner().invoke(cli, ["portal", "reset-pin"])
    assert r.exit_code == 0 and not sec.pin_is_set()
    assert c.get("/tasks").status_code == 200 or c.get("/tasks").status_code == 303  # sesión anterior invalidada o portal local sin PIN
    fresh = mk()
    assert "No debe perderse" in fresh.get("/tasks").text  # sin PIN el portal vuelve a ser solo-local y los datos siguen ahí
    assert fresh.post("/setup", data={"pin": "otra-clave-9", "pin2": "otra-clave-9"}).status_code == 303 and sec.pin_is_set()
    assert sec.check_pin("otra-clave-9") and not sec.check_pin("clave-larga-1")
    with db.session() as con:
        assert con.execute("SELECT COUNT(*) FROM audit_log WHERE action='portal.pin.reset'").fetchone()[0] == 1
    assert "¿Olvidaste el PIN?" in mk().get("/login").text
    # el restablecimiento no existe como ruta web: nadie puede hacerlo desde el navegador ni en remoto
    logged = mk()
    logged.post("/login", data={"pin": "otra-clave-9", "next": "/"})
    assert logged.post("/portal/reset-pin").status_code == 404 and logged.get("/reset-pin").status_code == 404


def test_real_browser_form_posts_are_accepted(app_env):
    """Regresión: Edge/Chrome envían `Origin: null` (con Referrer-Policy no-referrer) y `Sec-Fetch-Site: same-origin` en formularios propios."""
    mk, sec, _ = app_env
    c = mk()
    browser = {"origin": "http://127.0.0.1:8765", "sec-fetch-site": "same-origin"}
    assert c.post("/inbox/add", data={"text": "a"}, headers=browser).status_code == 303
    assert c.post("/inbox/add", data={"text": "b"}, headers={"origin": "null", "sec-fetch-site": "same-origin"}).status_code == 303
    assert c.post("/inbox/add", data={"text": "c"}, headers={"origin": "null", "sec-fetch-site": "none"}).status_code == 303
    assert c.post("/inbox/add", data={"text": "x"}, headers={"origin": "null", "sec-fetch-site": "cross-site"}).status_code == 403
    assert c.post("/inbox/add", data={"text": "x"}, headers={"origin": "null"}).status_code == 403  # null sin atestación: bloqueado
    r = c.post("/setup", data={"pin": "clave-larga-1", "pin2": "clave-larga-1"}, headers={"origin": "null", "sec-fetch-site": "same-origin"})
    assert r.status_code == 303 and sec.pin_is_set()
    assert c.get("/").headers["referrer-policy"] == "same-origin"


def test_cross_origin_post_and_no_secrets_exposed(app_env):
    mk, sec, _ = app_env
    c = mk()
    assert c.post("/inbox/add", data={"text": "x"}, headers={"origin": "http://evil.example.com"}).status_code == 403
    assert c.post("/inbox/add", data={"text": "x"}, headers={"sec-fetch-site": "cross-site"}).status_code == 403
    sec.set_pin("clave-larga-1")
    c.post("/login", data={"pin": "clave-larga-1", "next": "/"})
    stored = keyring.get_password("pos", "portal-pin")
    for path in ("/", "/system", "/jobs", "/tasks", "/knowledge", "/media"):
        body = c.get(path).text
        assert stored not in body and "portal-secret" not in body and "restic-password" not in body
    assert c.get("/api/today").status_code == 404 and c.get("/backup/key").status_code == 404
