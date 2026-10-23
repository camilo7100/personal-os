"""Base de conocimiento: notas Markdown en data/knowledge (repo Git propio) + índice FTS5 + tarjetas Anki."""
import hashlib
import re
import subprocess
import time
import unicodedata
from datetime import date
from pathlib import Path

import typer

from . import db
from .util import JSON, emit

app = typer.Typer(help="Conocimiento (notas Markdown)", no_args_is_help=True)
VAULT = db.DATA_DIR / "knowledge"
AREAS = ("inbox", "estudio", "trabajo", "personal", "proyectos", "referencia")


def slug(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-") or "nota"


def _git(*args: str) -> None:
    try:
        subprocess.run(["git", "-C", str(VAULT), *args], capture_output=True, timeout=60, check=False)
    except Exception:
        pass


def ensure_vault() -> None:
    VAULT.mkdir(parents=True, exist_ok=True)
    for a in AREAS:
        (VAULT / a).mkdir(exist_ok=True)
    if not (VAULT / ".git").exists():
        _git("init", "-q")


def create_note(title: str, area: str = "inbox", tags: str = "", body: str = "") -> Path:
    """Crea una nota con frontmatter en data/knowledge/<area>/."""
    if area not in AREAS:
        raise ValueError(f"área: {', '.join(AREAS)}")
    ensure_vault()
    path = VAULT / area / f"{date.today().isoformat()}-{slug(title)}.md"
    if path.exists():
        raise ValueError("ya existe una nota con ese título hoy")
    path.write_text(f"---\ntitle: {title}\ntags: [{tags}]\ncreated: {date.today().isoformat()}\n---\n\n# {title}\n\n{body}\n", encoding="utf-8")
    with db.session() as con:
        db.audit(con, "note.new", "WRITE", str(path.relative_to(VAULT)))
    return path


@app.command("new")
def new(title: str, area: str = "inbox", tags: str = "", body: str = "") -> None:
    try:
        typer.echo(str(create_note(title, area, tags, body)))
    except ValueError as e:
        raise typer.BadParameter(str(e))


def _parse(path: Path) -> tuple[str, str, str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    title, tags = path.stem, ""
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if line.startswith("title:"):
                title = line[6:].strip()
            elif line.startswith("tags:"):
                tags = line[5:].strip(" []")
    return title, tags, text


def index_notes() -> str:
    """Indexa notas nuevas/modificadas, elimina las borradas y versiona el vault en Git."""
    ensure_vault()
    files = {str(p.relative_to(VAULT)): p for p in VAULT.rglob("*.md") if ".git" not in p.parts}
    with db.session() as con:
        known = {r["path"]: r for r in con.execute("SELECT id, path, mtime FROM notes")}
        changed = 0
        for rel, p in files.items():
            mt = p.stat().st_mtime
            if rel in known and abs(known[rel]["mtime"] - mt) < 1e-6:
                continue
            title, tags, body = _parse(p)
            if rel in known:
                con.execute("UPDATE notes SET title=?, tags=?, mtime=?, body=? WHERE id=?", (title, tags, mt, body, known[rel]["id"]))
            else:
                con.execute("INSERT INTO notes(path, title, tags, mtime, body) VALUES (?,?,?,?,?)", (rel, title, tags, mt, body))
            changed += 1
        gone = [r["id"] for rel, r in known.items() if rel not in files]
        for i in gone:
            con.execute("DELETE FROM notes WHERE id=?", (i,))
    if changed or gone:
        _git("add", "-A")
        _git("-c", "user.name=PERSONAL_OS", "-c", "user.email=pos@localhost", "commit", "-q", "-m", f"notas: {changed} cambiadas, {len(gone)} eliminadas")
    return f"{len(files)} notas ({changed} indexadas, {len(gone)} eliminadas)"


@app.command("index")
def index_cmd() -> None:
    typer.echo(index_notes())


@app.command("search")
def search(query: str, limit: int = 10, as_json: bool = JSON) -> None:
    with db.session() as con:
        rows = con.execute(
            "SELECT n.path, n.title, snippet(notes_fts, 2, '[', ']', ' … ', 12) AS snippet FROM notes_fts f "
            "JOIN notes n ON n.id=f.rowid WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?", (query, limit)).fetchall()
    emit(rows, as_json)


# ---------- tarjetas Anki ----------
CARD = re.compile(r"^\s*(?:[-*]\s+)?(.+?)\s+::\s+(.+?)\s*$")


def extract_cards(path: Path) -> list[tuple[str, str]]:
    """Tarjetas con sintaxis `pregunta :: respuesta` (una por línea) dentro de una nota."""
    return [(m.group(1), m.group(2)) for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if (m := CARD.match(line)) and not line.lstrip().startswith("#")]


def build_deck(area: str = "estudio", out: Path | None = None) -> tuple[int, Path]:
    """Exporta a un mazo de Anki (.apkg) las tarjetas `pregunta :: respuesta` de un área. Se importa con doble clic."""
    import genanki
    ensure_vault()
    deck_id = int(hashlib.sha1(f"pos-{area}".encode()).hexdigest()[:8], 16)
    model = genanki.Model(1607392319, "PERSONAL_OS básico", fields=[{"name": "Pregunta"}, {"name": "Respuesta"}],
                          templates=[{"name": "Tarjeta", "qfmt": "{{Pregunta}}", "afmt": "{{FrontSide}}<hr id=answer>{{Respuesta}}"}])
    deck, n = genanki.Deck(deck_id, f"PERSONAL_OS::{area}"), 0
    for p in sorted((VAULT / area).rglob("*.md")):
        for q, a in extract_cards(p):
            deck.add_note(genanki.Note(model=model, fields=[q, a], guid=genanki.guid_for(area, q)))  # guid estable: reimportar no duplica
            n += 1
    if not n:
        raise ValueError("No hay tarjetas (usa `pregunta :: respuesta` en tus notas).")
    target = out or (db.DATA_DIR / f"anki-{area}.apkg")
    target.parent.mkdir(parents=True, exist_ok=True)
    genanki.Package(deck).write_to_file(str(target))
    return n, target


@app.command("cards")
def cards(area: str = "estudio", out: Path = typer.Option(None, help="Archivo .apkg de salida")) -> None:
    try:
        n, target = build_deck(area, out)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(1)
    typer.echo(f"{n} tarjetas -> {target}")
