"""Developer OS: estado de los repositorios Git de Documents\\GitHub\\ (rama, cambios sin guardar, adelanto/atraso)."""
import subprocess
from pathlib import Path

import typer

from . import db
from .util import JSON, emit

app = typer.Typer(help="Repositorios de desarrollo", no_args_is_help=True)
PROJECTS_DIR = Path.home() / "Documents" / "GitHub"


def _git(path: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    return r.stdout.strip() if r.returncode == 0 else ""


def scan(base: Path | None = None) -> str:
    base = base or PROJECTS_DIR
    found = []
    for d in sorted(p for p in base.iterdir() if p.is_dir() and (p / ".git").exists()):
        branch = _git(d, "rev-parse", "--abbrev-ref", "HEAD")
        dirty = len(_git(d, "status", "--porcelain").splitlines())
        counts = _git(d, "rev-list", "--left-right", "--count", "@{u}...HEAD").split()
        behind, ahead = (int(counts[0]), int(counts[1])) if len(counts) == 2 else (0, 0)
        found.append((str(d), d.name, branch, dirty, ahead, behind, _git(d, "log", "-1", "--format=%cs %s")[:120]))
    with db.session() as con:
        con.execute("DELETE FROM repos")
        con.executemany("INSERT INTO repos(path, name, branch, dirty, ahead, behind, last_commit) VALUES (?,?,?,?,?,?,?)", found)
    dirty_n = sum(1 for f in found if f[3])
    return f"{len(found)} repos, {dirty_n} con cambios sin guardar"


@app.command("scan")
def scan_cmd() -> None:
    typer.echo(scan())


@app.command("list")
def list_cmd(as_json: bool = JSON) -> None:
    with db.session() as con:
        emit(con.execute("SELECT name, branch, dirty, ahead, behind, last_commit FROM repos ORDER BY dirty DESC, name").fetchall(), as_json)
