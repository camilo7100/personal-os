import os

os.environ["POS_NO_NOTIFY"] = "1"  # los tests jamás envían toasts ni mensajes de Telegram reales

import pytest


@pytest.fixture(autouse=True)
def _keyring_aislado(monkeypatch):
    """Ningún test toca el Administrador de credenciales real (PIN, clave de backups): llavero en memoria."""
    import keyring
    store: dict = {}
    monkeypatch.setattr(keyring, "get_password", lambda s, u: store.get((s, u)))
    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: store.__setitem__((s, u), p))
    monkeypatch.setattr(keyring, "delete_password", lambda s, u: store.pop((s, u), None))


# Pruebas que necesitan programas externos: se saltan si no están instalados (p. ej. en el CI de GitHub).
def pytest_collection_modifyitems(config, items):  # noqa: D103
    import shutil

    import pytest

    necesita = {"test_backup.py": "restic", "test_modules.py::test_media_video_pipeline_with_ffmpeg": "ffmpeg"}
    for item in items:
        for patron, programa in necesita.items():
            if patron in item.nodeid.replace("\\", "/") and shutil.which(programa) is None:
                item.add_marker(pytest.mark.skip(reason=f"requiere {programa} (no instalado)"))
