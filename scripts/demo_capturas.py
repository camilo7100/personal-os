"""Capturas de pantalla del portal de DEMOSTRACIÓN (datos ficticios) para el README.

Requisitos: `pip install playwright` (usa el Edge ya instalado; no descarga navegadores) y el portal de la demo abierto:

    python scripts/demo_seed.py
    python scripts/demo_portal.py        # en otra terminal
    python scripts/demo_capturas.py      # guarda PNG en docs/img/
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "img"
BASE = "http://127.0.0.1:8790"
PAGES = [
    ("hoy", "/", "Hoy: lo que importa ahora, con la carga del día"),
    ("tareas", "/tasks?view=tablero", "Tareas en tablero"),
    ("calendario", "/calendar?view=week", "Calendario semanal"),
    ("finanzas", "/money", "Finanzas del mes: ingresos solo por trabajo, categorías y alertas"),
    ("finanzas-anual", "/money/year", "Resumen anual de finanzas"),
    ("planes", "/plans", "Planes con cronograma"),
    ("correo", "/mail", "Correo clasificado por reglas"),
    ("carrera", "/career", "Carrera: ofertas y postulaciones"),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    only = set(sys.argv[1:])
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2, color_scheme="dark", locale="es-CO")
        page = ctx.new_page()
        page.goto(f"{BASE}/login")
        page.fill("input[name=pin]", "demo-1234")
        page.click("button")
        page.wait_for_url(f"{BASE}/")
        for name, path, _ in PAGES:
            if only and name not in only:
                continue
            page.goto(BASE + path)
            page.wait_for_load_state("networkidle")
            page.evaluate(  # la demo no necesita el asistente de instalación
                """() => {
                    document.querySelectorAll('a').forEach(a => { if (/Primeros pasos/.test(a.textContent) && a.closest('nav, aside')) a.remove(); });
                    document.querySelectorAll('.note, .banner').forEach(n => { if (/Faltan \d+ pasos/.test(n.textContent)) n.remove(); });
                }"""
            )
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=False)
            print("capturada:", name)
        browser.close()


if __name__ == "__main__":
    main()
