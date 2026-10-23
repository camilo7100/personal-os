import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("POS_DB", str(tmp_path / "pos.db"))
    monkeypatch.setenv("POS_HOME", str(tmp_path))
    for m in [m for m in sys.modules if m == "pos" or m.startswith("pos.")]:
        del sys.modules[m]
    from pos import db, notes
    db.init_db()
    monkeypatch.setattr(notes, "VAULT", tmp_path / "knowledge")
    return db, tmp_path


# ---------------- finanzas ----------------
@pytest.mark.parametrize("raw,expected", [("1.234,56", 1234.56), ("1,234.56", 1234.56), ("12.500", 12500), ("-3500", -3500),
                                          ("(3.500)", -3500), ("$ 1.000.000", 1000000), ("45,5", 45.5), ("", 0)])
def test_parse_amount(env, raw, expected):
    from pos.money import parse_amount
    assert parse_amount(raw) == expected


def test_money_import_dedupe_categories_and_budget(env, tmp_path):
    db, _ = env
    from pos import money
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "money_rules.json").write_text(json.dumps({"Mercado": ["exito"]}))
    money.RULES_FILE = tmp_path / "config" / "money_rules.json"
    f = tmp_path / "ext.csv"
    f.write_text("Fecha;Descripcion;Debito;Credito\n01/09/2026;EXITO PALMIRA;45.000;\n01/09/2026;Café;5.000;\n01/09/2026;Café;5.000;\n02/09/2026;Nómina;;3.000.000\n", encoding="utf-8")
    with db.session() as con:
        con.execute("INSERT INTO accounts(name) VALUES ('Ahorros')")
        assert money.import_file(con, f, "Ahorros", debit_col="Debito", credit_col="Credito") == (4, 0)  # los dos cafés iguales cuentan
        assert money.import_file(con, f, "Ahorros", debit_col="Debito", credit_col="Credito") == (0, 4)  # reimportar no duplica
        con.execute("INSERT INTO budgets(category, monthly_amount) VALUES ('Mercado', 40000)")
        s = money.month_summary(con, "2026-09")
    assert s["income"] == 3000000 and s["expense"] == 55000
    assert {c["category"]: c["over_budget"] for c in s["by_category"]}["Mercado"] is True


# ---------------- vida diaria ----------------
def test_habits_streak_and_upcoming(env):
    db, _ = env
    from pos import life
    today = date.today()
    with db.session() as con:
        life.add_habit(con, "Caminar")
        for i in (1, 2, 3):  # ayer, antier, hace 3 días
            con.execute("INSERT INTO habit_log VALUES (1, ?)", ((today - timedelta(days=i)).isoformat(),))
        assert life.habit_status(con)[0]["streak"] == 3 and life.habit_status(con)[0]["done_today"] is False
        assert life.toggle_habit(con, 1) is True and life.habit_status(con)[0]["streak"] == 4
        life.add_doc(con, "Pasaporte", (today + timedelta(days=10)).isoformat())
        life.add_sub(con, "Netflix", 30000, next_date=(today + timedelta(days=3)).isoformat())
        life.add_person(con, "Ana", birthday=(today + timedelta(days=5)).strftime("%m-%d"))
        u = life.upcoming(con, 14)
    assert len(u["documents"]) == len(u["subscriptions"]) == len(u["birthdays"]) == 1


# ---------------- briefing / carrera ----------------
def test_briefing_generates_files(env, monkeypatch):
    db, tmp = env
    from pos import briefing, career, services
    monkeypatch.setattr(briefing, "OUT", tmp / "briefings")
    with db.session() as con:
        services.add_task(con, "Vencida", due="2000-01-01", prio=1)
        services.add_inbox(con, "algo")
        career.add_application(con, "Acme", "Analista", status="applied", next_step="Llamar", next_step_date=(date.today() + timedelta(days=2)).isoformat())
        assert career.board(con)[0]["company"] == "Acme"
    for kind in ("morning", "evening", "weekly"):
        assert briefing.write(kind)
    md = (tmp / "briefings" / f"{date.today().isoformat()}-morning.md").read_text(encoding="utf-8")
    assert "Vencida" in md and "Acme" in md and "Inbox sin clasificar: **1**" in md


# ---------------- conocimiento ----------------
def test_notes_create_index_search_cards(env):
    db, tmp = env
    from pos import notes
    p = notes.create_note("Clustering jerárquico", "estudio", "ml", "linkage :: define la distancia entre grupos\nPCA :: reduce dimensiones")
    assert "linkage" in p.read_text(encoding="utf-8")
    assert "1 indexadas" in notes.index_notes()
    with db.session() as con:
        assert con.execute("SELECT COUNT(*) FROM notes_fts WHERE notes_fts MATCH 'linkage'").fetchone()[0] == 1
    assert len(notes.extract_cards(p)) == 2
    n, out = notes.build_deck("estudio", tmp / "deck.apkg")
    assert n == 2 and out.exists()
    p.unlink()
    assert "1 eliminadas" in notes.index_notes()


def test_inbox_note_resolution(env):
    db, _ = env
    from pos import services, notes
    with db.session() as con:
        i = services.add_inbox(con, "Idea: reporte semanal automático")
        assert services.resolve_inbox(con, i, "note", title="Reporte semanal").startswith("note:inbox")
    assert any(notes.VAULT.glob("inbox/*.md"))


# ---------------- desarrollo ----------------
def test_repos_scan(env, tmp_path):
    db, _ = env
    from pos import repos
    base = tmp_path / "Proyectos"
    (base / "demo").mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", "-C", str(base / "demo"), *a], capture_output=True, check=True)
    run("init", "-q"); (base / "demo" / "a.txt").write_text("x")
    run("add", "."); run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "primero")
    (base / "demo" / "b.txt").write_text("y")
    assert "1 repos, 1 con cambios" in repos.scan(base)


# ---------------- triage y Telegram (simulado) ----------------
def test_triage_classifies_spanish_captures(env):
    from datetime import date
    from pos import triage
    today = date(2026, 9, 29)  # martes
    c = triage.classify("Llamar al contador el viernes por la declaración de renta", ["Taller Demo"], today)
    assert (c["kind"], c["due"]) == ("task", "2026-10-02")
    assert triage.classify("Idea: automatizar el reporte de ventas de Taller Demo", ["Taller Demo"], today)["kind"] == "note"
    assert triage.classify("Comprar cable HDMI mañana urgente", [], today) == {"kind": "task", "title": "Comprar cable HDMI mañana urgente", "due": "2026-09-30", "project": None, "priority": 1}
    assert triage.classify("Pagar arriendo el 5", [], today)["due"] == "2026-10-05"
    assert triage.classify("revisar contrato de Taller Demo", ["Taller Demo"], today)["project"] == "Taller Demo"
    assert triage.classify("https://ejemplo.com/articulo", [], today)["kind"] == "note"
    assert triage.classify("asdf", [], today)["kind"] == "discard"
    assert triage.parse_due("en 3 dias", today) == "2026-10-02" and triage.parse_due("15 de octubre", today) == "2026-10-15"


def test_triage_job_stores_suggestions(env):
    db, _ = env
    from pos import services, triage
    with db.session() as con:
        services.add_inbox(con, "Llamar a Ana mañana")
    assert triage.run() == "1 sugerencias" and triage.run() == "nada que clasificar"
    with db.session() as con:
        assert json.loads(con.execute("SELECT suggestion FROM inbox").fetchone()[0])["kind"] == "task"


def test_telegram_capture_only_from_authorized_chat(env, monkeypatch):
    db, _ = env
    from pos import telegram
    sent = []
    monkeypatch.setattr(telegram, "configured", lambda: True)
    monkeypatch.setattr(telegram, "_chat", lambda: "42")
    monkeypatch.setattr(telegram, "send", lambda t: sent.append(t) or True)
    monkeypatch.setattr(telegram, "api", lambda method, params=None, **k: {"result": [
        {"update_id": 1, "message": {"chat": {"id": 42}, "text": "Pagar el arriendo el 5"}},
        {"update_id": 2, "message": {"chat": {"id": 999}, "text": "intruso"}}]})
    assert telegram.capture() == "1 capturas nuevas"
    with db.session() as con:
        assert [r[0] for r in con.execute("SELECT text FROM inbox")] == ["Pagar el arriendo el 5"]
        assert con.execute("SELECT value FROM meta WHERE key='telegram_offset'").fetchone()[0] == "3"
    assert sent == ["✓ capturado #1"]


# ---------------- multimedia ----------------
def test_media_images_and_watch_folder(env, tmp_path, monkeypatch):
    db, _ = env
    from PIL import Image
    from pos import media
    monkeypatch.setattr(media, "MEDIA_HOME", tmp_path / "Media")
    media.ensure_folders()
    Image.new("RGB", (3000, 2000), "blue").save(tmp_path / "Media" / "4-Imagenes-reducir-1600px" / "foto.jpg")
    Image.new("RGB", (800, 600), "red").save(tmp_path / "Media" / "5-Imagenes-a-WebP" / "logo.png")
    assert media.watch(min_age_s=0) == "2 archivos procesados"
    out = {p.name for p in (tmp_path / "Media" / "Salida").iterdir()}
    assert out == {"foto-1600px.jpg", "logo-web.webp"}
    assert Image.open(tmp_path / "Media" / "Salida" / "foto-1600px.jpg").width == 1600
    assert (tmp_path / "Media" / "Originales-procesados" / "foto.jpg").exists()


def test_media_video_pipeline_with_ffmpeg(env, tmp_path):
    from pos import media
    ff = media.ffmpeg_exe()
    src = tmp_path / "clip.mp4"
    subprocess.run([ff, "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x180:rate=15", "-f", "lavfi", "-i", "sine=duration=1",
                    "-c:v", "libx264", "-c:a", "aac", str(src)], capture_output=True, check=True)
    assert media.video_compress(src).exists() and media.video_audio(src).suffix == ".mp3" and media.video_gif(src, 160).exists()


def test_panel_static_copy_has_no_scripts_and_encrypts_with_the_key(tmp_path, monkeypatch):
    import gzip, json, sys
    monkeypatch.setenv("POS_DB", str(tmp_path / "pos.db"))
    monkeypatch.setenv("POS_HOME", str(tmp_path))
    for m in [m for m in sys.modules if m == "pos" or m.startswith("pos.")]:
        del sys.modules[m]
    import keyring
    store = {}
    monkeypatch.setattr(keyring, "get_password", lambda s, n: store.get((s, n)))
    monkeypatch.setattr(keyring, "set_password", lambda s, n, v: store.__setitem__((s, n), v))
    from pos import db, panel
    db.init_db()
    assert panel.key_bytes() is None
    k = panel.new_key()
    assert len(panel.key_bytes()) == 32 and k == panel.key_text()
    raw = '<link rel="stylesheet" href="/static/style.css"><script src="/static/x.js"></script><a class="x" href="/tasks?view=tablero">t</a><a href="/logout">salir</a><form action="/money/go"><select></select></form>'
    out = panel._static_page(raw, "body{}", "<nav>PICK</nav>")
    assert "<script" not in out and "<style>body{}</style>" in out and "PICK" in out
    assert 'href="#/tasks?view=tablero" target="_top"' in out and 'href="#/" target="_top"' in out      # enlaces internos dentro del visor
    assert panel.config()["url"].startswith("https://") and panel.pair_link().endswith("#k=" + k)       # la llave viaja solo en el #fragmento
