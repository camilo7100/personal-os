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
