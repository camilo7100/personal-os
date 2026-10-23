import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setenv("POS_DB", str(proj / "data" / "pos.db"))
    monkeypatch.setenv("POS_HOME", str(proj))
    for m in [m for m in sys.modules if m == "pos" or m.startswith("pos.")]:
        del sys.modules[m]
    from pos import backup, cleanup, db, notify
    db.init_db()
    monkeypatch.setattr(notify.subprocess, "run", lambda *a, **k: None)
    bak = tmp_path / "PERSONAL_OS_BACKUP"
    (proj / "config").mkdir()
    (proj / "config" / "backup.json").write_text(json.dumps(
        {"essential": {"repo": str(bak / "essential")}, "external": {}, "excludes": []}))
    monkeypatch.setattr(backup, "CONFIG", proj / "config" / "backup.json")
    (bak / "essential").mkdir(parents=True)
    return cleanup, db, proj, bak


def test_cleanup_deletes_regenerable_and_reports_mess(env):
    cleanup, db, root, bak = env
    (root / "scripts" / "__pycache__").mkdir(parents=True)
    (root / "scripts" / "__pycache__" / "x.pyc").write_text("x")
    (root / "empty_dir").mkdir()
    (root / "README.md").write_text("ok")
    (root / "ARCHITECTURE_V2.md").write_text("dup")
    (root / "notas_old.txt").write_text("basura")
    (bak / "restic").mkdir()  # repo huérfano
    msg = cleanup.run()
    assert not (root / "scripts" / "__pycache__").exists() and not (root / "empty_dir").exists()
    assert "ARCHITECTURE_V2.md" in msg and "notas_old" in msg and "huérfano" in msg
    assert (root / "notas_old.txt").exists()  # lo dudoso se reporta, no se borra solo
    with db.session() as con:
        assert con.execute("SELECT COUNT(*) FROM alerts WHERE title LIKE 'cleanup%'").fetchone()[0] == 1


def test_cleanup_applies_retention(env):
    cleanup, db, *_ = env
    with db.session() as con:
        con.execute("INSERT INTO health(ts, metric, value) VALUES (datetime('now','-200 day'), 'cpu_pct', 1)")
        con.execute("INSERT INTO health(metric, value) VALUES ('cpu_pct', 2)")
    assert "1 filas vencidas" in cleanup.run()
    with db.session() as con:
        assert con.execute("SELECT COUNT(*) FROM health").fetchone()[0] == 1
