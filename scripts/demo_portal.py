"""Abre el portal con la base de DEMOSTRACIÓN (datos ficticios) en http://127.0.0.1:8790.

No toca tu base real, ni tu PIN, ni el Administrador de credenciales: usa un llavero en memoria.
PIN de la demo: demo-1234

    python scripts/demo_seed.py     # una vez
    python scripts/demo_portal.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "demo"
os.environ["POS_HOME"] = str(DEMO)
os.environ["POS_DB"] = str(DEMO / "pos.db")
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "portal"))

import keyring  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402


class MemoryKeyring(KeyringBackend):
    """Llavero en memoria: la demo nunca escribe en el Administrador de credenciales."""

    priority = 99

    def __init__(self) -> None:
        super().__init__()
        self._d: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):  # noqa: ANN001
        return self._d.get((service, username))

    def set_password(self, service, username, password):  # noqa: ANN001
        self._d[(service, username)] = password

    def delete_password(self, service, username):  # noqa: ANN001
        self._d.pop((service, username), None)


keyring.set_keyring(MemoryKeyring())

import uvicorn  # noqa: E402
from pos import portal_security as sec  # noqa: E402

if __name__ == "__main__":
    if not (DEMO / "pos.db").exists():
        sys.exit("Primero: python scripts/demo_seed.py")
    sec.set_pin("demo-1234")
    from app import app  # noqa: E402

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("DEMO_PORT", "8790")), log_level="warning")
