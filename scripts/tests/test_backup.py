import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("POS_DB", str(tmp_path / "pos.db"))
    monkeypatch.setenv("POS_HOME", str(tmp_path))
    monkeypatch.delenv("POS_LIVE", raising=False)
    for m in [m for m in sys.modules if m == "pos" or m.startswith("pos.")]:
        del sys.modules[m]
    from pos import backup, db
    from pos.cli import app
    db.init_db()
    src, bulk, disk = tmp_path / "critico", tmp_path / "pesado", tmp_path / "disco_usb"
    for d in (src, bulk, disk):
        d.mkdir()
    for i in range(12):  # varios archivos de texto para que restore-test tenga de dónde elegir
        (src / f"c{i}.txt").write_text(f"critico {i}" * 20)
        (bulk / f"b{i}.txt").write_text(f"pesado {i}" * 20)
    (disk / backup.MARKER).write_text("x")
    cfg = {"essential": {"repo": str(tmp_path / "local_repo"), "sources": [str(src)], "exclude_larger_than": "1M", "max_gb": 1.0, "keep_local_snapshots": 1},
           "external": {"essential_repo_dir": "ext_ess", "full_repo_dir": "ext_full", "min_free_gb": 0, "full_sources": [str(bulk)]},
           "excludes": []}
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "backup.json").write_text(json.dumps(cfg))
    monkeypatch.setattr(backup, "CONFIG", tmp_path / "config" / "backup.json")
    monkeypatch.setattr(backup, "_candidate_drives", lambda: [{"mount": str(disk), "total_gb": 1, "free_gb": 1}])
    monkeypatch.setattr(backup.psutil, "disk_usage", lambda p: type("U", (), {"free": 10e9, "total": 20e9})())
    return backup, db, CliRunner(), app, tmp_path, disk


def test_recurring_task_creates_next(env):
    _, db, _, _, _, _ = env
    with db.session() as con:
        con.execute("INSERT INTO tasks(title, due_date, recurrence) VALUES ('mensual','2026-01-31','monthly')")
        nxt = db.complete_task(con, 1)
        assert con.execute("SELECT due_date FROM tasks WHERE id=?", (nxt,)).fetchone()[0] == "2026-02-28"
        assert db.complete_task(con, 1) is None  # ya estaba hecha


def test_external_flow_copies_verifies_cuts_and_closes_task(env):
    backup, db, _, _, tmp_path, disk = env
    with db.session() as con:
        con.execute("INSERT INTO tasks(title, due_date, recurrence) VALUES (?, '2026-10-01', 'monthly')", (backup.TASKS["essential"],))
    msg = backup.run_external("essential")
    assert "destino" in msg and "integridad ok" in msg
    ext = str(disk / "ext_ess")
    tags = backup._restic(ext, "snapshots", "--json").stdout
    assert len(json.loads(tags)) == 1  # el snapshot esencial preparado se copió al disco
    # segunda ejecución: el local se recorta a 1 snapshot ("cortar"), el externo conserva el historial
    backup.run_external("essential")
    local = json.loads(backup._restic(str(tmp_path / "local_repo"), "snapshots", "--json").stdout)
    assert len(local) == 1
    with db.session() as con:
        rows = con.execute("SELECT status, due_date FROM tasks WHERE title=? ORDER BY id", (backup.TASKS["essential"],)).fetchall()
    assert [r["status"] for r in rows][:2] == ["done", "done"] and rows[-1]["status"] == "todo"


def test_full_backup_goes_to_its_own_repo_and_is_incremental(env):
    backup, db, _, _, tmp_path, disk = env
    with db.session() as con:
        con.execute("INSERT INTO tasks(title, due_date, recurrence) VALUES (?, '2026-10-12', 'quarterly')", (backup.TASKS["full"],))
    assert "integridad ok" in backup.run_external("full")
    (tmp_path / "pesado" / "nuevo.txt").write_text("archivo nuevo" * 20)
    backup.run_external("full")
    snaps = json.loads(backup._restic(str(disk / "ext_full"), "snapshots", "--json").stdout)
    assert len(snaps) == 2 and not (disk / "ext_ess").exists()  # repos separados
    with db.session() as con:
        assert con.execute("SELECT COUNT(*) FROM tasks WHERE title=? AND status='todo'", (backup.TASKS["full"],)).fetchone()[0] == 1


def test_essential_excludes_big_files(env):
    backup, _, _, _, tmp_path, _ = env
    (tmp_path / "critico" / "grande.bin").write_bytes(b"x" * 2_000_000)  # tope de prueba: 1M
    backup.run()
    files = [json.loads(l).get("path", "") for l in backup._restic(str(tmp_path / "local_repo"), "ls", "latest", "--json").stdout.splitlines() if l.startswith("{")]
    assert not any(p.endswith("grande.bin") for p in files) and any(p.endswith("c1.txt") for p in files)


def test_external_requires_marked_disk(env, monkeypatch):
    backup, _, _, _, _, disk = env
    (disk / backup.MARKER).unlink()
    with pytest.raises(RuntimeError, match="no reconocido"):
        backup.find_external(False)
