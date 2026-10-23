import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "portal"))

GET_ROUTES = ["/", "/inbox", "/tasks", "/tasks?status=done", "/projects", "/goals", "/mail", "/mail?q=factura", "/study", "/plans", "/ask", "/knowledge",
              "/knowledge?q=hola", "/manual", "/calendar", "/calendar?view=week", "/calendar?view=day", "/calendar?view=agenda", "/calendar?view=timeline", "/tasks?view=tablero", "/tasks?view=matriz", "/tasks?view=todas", "/tasks?view=cualquier", "/tasks?view=algun-dia", "/plan/day", "/review", "/devices", "/career", "/money", "/money/year", "/money/year?year=2019", "/life", "/media", "/dev", "/jobs", "/system", "/login"]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("POS_DB", str(tmp_path / "pos.db"))
    monkeypatch.setenv("POS_HOME", str(tmp_path))
    for m in [m for m in sys.modules if m in ("pos", "app") or m.startswith("pos.")]:
        del sys.modules[m]
    from pos import db, media, notes
    db.init_db()
    monkeypatch.setattr(media, "MEDIA_HOME", tmp_path / "media")
    monkeypatch.setattr(notes, "VAULT", tmp_path / "knowledge")
    import app as portal
    monkeypatch.setattr(portal, "scheduler_info", lambda: {})
    return TestClient(portal.app, base_url="http://127.0.0.1:8765", follow_redirects=False)


def test_every_page_renders_empty(client):
    for r in GET_ROUTES:
        resp = client.get(r)
        assert resp.status_code == 200, f"{r} -> {resp.status_code}"


def test_rejects_foreign_host_and_origin(client):
    assert client.get("/", headers={"host": "evil.example.com"}).status_code == 403
    assert client.post("/inbox/add", data={"text": "x"}, headers={"origin": "http://evil.example.com"}).status_code == 403


def test_capture_and_task_flow(client):
    assert client.post("/inbox/add", data={"text": "Comprar café"}).status_code == 303
    assert "Comprar café" in client.get("/inbox").text
    assert client.post("/inbox/1/resolve", data={"kind": "task", "title": "Comprar café", "due": "2000-01-01"}).status_code == 303
    page = client.get("/tasks").text
    assert "Comprar café" in page and "2000-01-01" in page
    assert client.post("/tasks/1/done", data={"back": "/tasks"}).status_code == 303
    assert "Comprar café" not in client.get("/tasks").text and "Comprar café" in client.get("/tasks?status=done").text


def test_all_modules_write_and_show(client, tmp_path):
    client.post("/projects/add", data={"name": "Maestría", "area": "estudio"})
    client.post("/goals/add", data={"title": "Terminar la maestría", "horizon": "year"})
    client.post("/life/habit", data={"name": "Caminar"})
    client.post("/habits/1/toggle")
    client.post("/life/doc", data={"name": "Pasaporte", "expires": "2030-01-01"})
    client.post("/life/sub", data={"name": "Netflix", "amount": "30000", "next_date": "2030-01-01"})
    client.post("/life/person", data={"name": "Ana", "birthday": "01-01"})
    client.post("/career/add", data={"company": "Acme", "role": "Analista de datos"})
    client.post("/money/account", data={"name": "Ahorros"})
    csv = "fecha;descripcion;monto\n01/09/2026;Exito mercado;-45.000\n02/09/2026;Nomina;3.000.000\n".encode()
    r = client.post("/money/import", data={"account": "Ahorros", "delimiter": ";", "amount_col": "monto"}, files={"file": ("x.csv", csv)})
    assert r.status_code == 303
    client.post("/knowledge/new", data={"title": "Clustering", "area": "estudio", "body": "k-means :: agrupa por distancia"})
    for path, expected in [("/projects", "Maestría"), ("/goals", "Terminar la maestría"), ("/life", "Pasaporte"), ("/life", "Netflix"),
                           ("/life", "Ana"), ("/career", "Acme"), ("/money?month=2026-09", "3.000.000"), ("/money?month=2026-09", "45.000"),
                           ("/study", "1"), ("/knowledge", "Clustering")]:
        assert expected in client.get(path).text, f"{path} sin {expected}"
    from pos import briefing, db
    with db.session() as con:
        assert briefing.gather(con)["habits"][0]["done_today"] is True


def test_study_deck_download(client):
    client.post("/knowledge/new", data={"title": "Repaso", "area": "estudio", "body": "PCA :: reduce dimensiones\nk-means :: agrupa"})
    r = client.post("/study/deck")
    assert r.status_code == 200 and r.content[:2] == b"PK"  # .apkg es un zip


def test_path_traversal_blocked(client):
    assert client.get("/knowledge/view?path=../../pos.db").status_code == 404


def test_service_worker_is_served_from_root_and_clears_on_logout(client):
    r = client.get("/sw.js")
    assert r.status_code == 200 and r.headers["service-worker-allowed"] == "/" and "javascript" in r.headers["content-type"]
    js = r.text
    assert "/money" in js.split("const OK = ")[1].split("];")[0]                # todo se puede ver sin conexión en el equipo emparejado
    assert "caches.delete" in js and "/logout" in js                            # cerrar sesión borra lo guardado
    assert client.get("/static/manifest.webmanifest").status_code == 200


def test_year_summary_and_single_period_picker(client):
    from pos import db
    with db.session() as con:
        con.execute("INSERT INTO accounts(name, kind) VALUES ('Bancolombia ahorros', 'savings')")
        for d, desc, amt, cat in (("2026-01-15", "PAGO DE NOMI X", 5000000, "Salario"), ("2026-01-20", "EXITO", -400000, "Mercado"),
                                  ("2026-04-15", "PAGO DE NOMI X", 5000000, "Salario"), ("2026-04-22", "UBER", -100000, "Transporte y gasolina")):
            con.execute("INSERT INTO transactions(account_id, date, description, amount, category, hash) VALUES (1,?,?,?,?,?)", (d, desc, amt, cat, desc + d))
    page = client.get("/money/year?year=2026")
    assert page.status_code == 200 and "Finanzas 2026" in page.text and "Mercado" in page.text and "T1" in page.text and "T2" in page.text
    assert client.get("/money/go?period=2026").headers["location"] == "/money/year?year=2026"          # elegir un año lleva a su resumen
    assert client.get("/money/go?period=2026-04&entity=personal").headers["location"] == "/money?month=2026-04&entity=personal"
    assert "Año 2026" in client.get("/money").text and "Año 2026" in page.text                          # el mismo selector en las dos páginas
