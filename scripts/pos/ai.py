"""IA local (N3) con Ollama, solo donde aporta valor y no basta un script: visión y OCR de imágenes/PDF escaneados.
Nada sale del equipo. (Medido: el modelo razona antes de responder, ~13 tokens/s; por eso no se usa para clasificar texto.)"""
import base64
import json
import urllib.request
from pathlib import Path

import typer

from . import db

app = typer.Typer(help="IA local (Ollama): visión y OCR", no_args_is_help=True)
CONFIG = db.ROOT / "config" / "ai.json"
DEFAULTS = {"model": "qwen3-vl:8b", "url": "http://127.0.0.1:11434", "keep_alive": "5m"}


def cfg() -> dict:
    try:
        return {**DEFAULTS, **json.loads(CONFIG.read_text(encoding="utf-8"))}
    except Exception:
        return dict(DEFAULTS)


def generate(prompt: str, images: list[Path] | None = None, timeout: int = 900, max_tokens: int = 4000) -> str:
    c = cfg()
    body = {"model": c["model"], "prompt": prompt, "stream": False, "keep_alive": c["keep_alive"],
            "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": 8192}}
    if images:
        body["images"] = [base64.b64encode(Path(i).read_bytes()).decode() for i in images]
    req = urllib.request.Request(c["url"] + "/api/generate", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["response"].strip()


def available() -> bool:
    try:
        urllib.request.urlopen(cfg()["url"] + "/api/version", timeout=3).read()
        return True
    except Exception:
        return False


def ocr_image(path: Path) -> str:
    return generate("Transcribe todo el texto visible de esta imagen, respetando el orden y la estructura. Solo el texto.", [path])


@app.command("status")
def status() -> None:
    typer.echo(f"Ollama: {'activo' if available() else 'NO responde'} · modelo={cfg()['model']}")


@app.command("ocr")
def ocr_cmd(file: Path) -> None:
    """Texto de una imagen (lento en este equipo: úsalo con imágenes que lo justifiquen)."""
    typer.echo(ocr_image(file))
