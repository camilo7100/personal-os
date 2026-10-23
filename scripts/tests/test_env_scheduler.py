import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_paths_work_when_task_scheduler_gives_no_appdata(tmp_path, monkeypatch):
    """Regresión: el Programador de tareas no define APPDATA; el correo dejaba de encontrar Thunderbird."""
    home = tmp_path / "usuario"
    (home / "AppData" / "Roaming" / "Thunderbird" / "Profiles" / "x1.pos-mail").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("POS_DB", str(tmp_path / "pos.db"))
    monkeypatch.setenv("POS_HOME", str(tmp_path))
    for m in [m for m in sys.modules if m == "pos" or m.startswith("pos.")]:
        del sys.modules[m]
    from pos import db, mail, media, setup
    assert db.APPDATA == home / "AppData" / "Roaming" and db.LOCALAPPDATA == home / "AppData" / "Local"
    assert mail._find_profile() is not None and mail._find_profile().name == "x1.pos-mail"
    assert str(setup.THUNDERBIRD).startswith(str(home)) and str(setup.STARTUP).startswith(str(home))
    assert str(media._LOCAL) == str(home / "AppData" / "Local")
