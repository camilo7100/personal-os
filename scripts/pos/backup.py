"""Backups con restic (cifrado y deduplicado). Contraseña en Credential Manager; configuración en config/backup.json.

Dos escenarios, dos repositorios separados en el disco externo:
- ESENCIAL (~0,4 GB, tope 1 GB): datos del sistema, proyectos y estudio; archivos >25 MB excluidos. Se prepara en C: cada
  semana (job 'backup') y el doble clic Backup-Esencial.cmd lo TRASLADA al disco externo ('restic copy') y recorta lo local.
- COMPLETO: todo el PC (Documentos, Imágenes, Videos…) directo del origen al disco, solo cuando el usuario lo pide
  (Backup-Completo.cmd). No se prepara en C: porque llenaría el disco; después de la primera vez es incremental.
El disco externo se reconoce por el marcador '.pos-backup-disk' (no depende de la letra de unidad)."""
import json
import os
import secrets
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

import keyring
import psutil
import typer

from . import db

app = typer.Typer(help="Backup (restic)", no_args_is_help=True)
CONFIG = db.ROOT / "config" / "backup.json"
SERVICE, USER = "pos", "restic-password"
HOME = Path.home()
MARKER = ".pos-backup-disk"
TASKS = {"essential": "Backup esencial al disco externo (doble clic en Backup-Esencial)",
         "full": "Backup completo del PC al disco externo (doble clic en Backup-Completo)"}
DEFAULT = {
    "essential": {
        "repo": str(HOME / "PERSONAL_OS_BACKUP" / "essential"),
        "sources": [str(db.ROOT / "data"), str(db.ROOT / "config"), str(HOME / "Documents" / "GitHub"),
                    str(HOME / "Documents" / "Maestria")],
        "exclude_larger_than": "25M",
        "max_gb": 1.0,
        "keep_local_snapshots": 2,
        # Copia cifrada FUERA del PC: carpeta de OneDrive (restic cifra antes de subir; OneDrive no puede leer nada).
        # Vacío = desactivada. Protege si el PC y el disco externo fallan a la vez.
        "offsite_repo": str(HOME / "OneDrive" / "PERSONAL_OS_BACKUP" / "essential") if (HOME / "OneDrive").exists() else "",
    },
    "external": {
        "essential_repo_dir": "PERSONAL_OS_BACKUP\\essential",
        "full_repo_dir": "PERSONAL_OS_BACKUP\\full",
        "min_free_gb": 50,
        "full_sources": [str(db.ROOT / "data"), str(db.ROOT / "config"), str(HOME / "Documents"),  # Documents ya incluye Documents\GitHub
                         str(HOME / "Pictures"), str(HOME / "Videos"), str(HOME / "Music"), str(HOME / "Desktop"),
                         str(HOME / "Downloads")],
    },
    "excludes": ["node_modules", ".venv", "__pycache__", "*.tmp", "Thumbs.db", "$RECYCLE.BIN", "mail-profile", "pos.db", "pos.db-wal", "pos.db-shm"],
}


def load_config() -> dict:
    if not CONFIG.exists():
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(DEFAULT, indent=2, ensure_ascii=False), encoding="utf-8")
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    for k, v in DEFAULT["essential"].items():  # claves nuevas en configuraciones antiguas
        cfg["essential"].setdefault(k, v)
    cfg["excludes"] = sorted(set(cfg.get("excludes", [])) | {"mail-profile", "pos.db", "pos.db-wal", "pos.db-shm"})  # el perfil de correo lleva sesiones (tokens) y ya vive en la nube
    return cfg


def restic_exe() -> str:
    exe = shutil.which("restic")
    if exe:
        return exe
    base = db.LOCALAPPDATA / "Microsoft" / "WinGet" / "Packages"
    for p in base.glob("restic.restic_*/restic*.exe"):
        return str(p)
    raise RuntimeError("restic no encontrado")


def _password() -> str:
    pw = keyring.get_password(SERVICE, USER)
    if not pw:
        pw = secrets.token_urlsafe(32)
        keyring.set_password(SERVICE, USER, pw)
    return pw


def _err(r: subprocess.CompletedProcess) -> str:
    """Primeras líneas de stderr (el motivo real), no el traceback de Go."""
    return " ".join((r.stderr or "").strip().splitlines()[:2])[:300]


def _restic(repo: str, *args: str, timeout: int = 4 * 3600, live: bool = False) -> subprocess.CompletedProcess:
    env = {**os.environ, "RESTIC_PASSWORD": _password(), "RESTIC_FROM_PASSWORD": _password(), "RESTIC_REPOSITORY": repo}
    kw = {} if live else {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
    return subprocess.run([restic_exe(), *args], env=env, timeout=timeout, **kw)


def ensure_repo(repo: str, chunker_from: str | None = None) -> None:
    """chunker_from: repo origen cuyo chunker se copia, requisito para poder usar 'restic copy' sin duplicar datos."""
    if _restic(repo, "cat", "config", timeout=120).returncode != 0:
        extra = ["--from-repo", chunker_from, "--copy-chunker-params"] if chunker_from else []
        r = _restic(repo, "init", *extra, timeout=300)
        if r.returncode != 0 and "already exists" not in (r.stderr or ""):  # OneDrive a veces tarda en mostrar el config existente
            raise RuntimeError("restic init: " + _err(r))


def _backup(repo: str, sources: list[str], excludes: list[str], max_file: str | None = None, live: bool = False) -> str:
    sources = [s for s in sources if Path(s).exists()]
    args = ["backup", *sources, "--tag", "pos"]
    if max_file:
        args += ["--exclude-larger-than", max_file]
    for ex in excludes:
        args += ["--exclude", ex]
    r = _restic(repo, *args, live=live)
    if r.returncode not in (0, 3):  # 3 = algunos archivos ilegibles; el snapshot sí se creó
        raise RuntimeError("restic backup: " + (_err(r) if not live else f"código {r.returncode}"))
    if live:
        return f"snapshot creado (código {r.returncode})"
    return ([l for l in r.stdout.splitlines() if "snapshot" in l.lower()][-1:] or ["ok"])[0].strip()


def snapshot_db() -> Path:
    """Copia CONSISTENTE de la base (API de respaldo de SQLite) para que el backup nunca capture un archivo a medio escribir."""
    import sqlite3
    dst = db.DATA_DIR / "db-snapshot" / "pos-snapshot.db"
    dst.parent.mkdir(parents=True, exist_ok=True)
    src, out = sqlite3.connect(db.DB_PATH), sqlite3.connect(dst)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    return dst


def repo_gb(path: str) -> float:
    p = Path(path)
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e9 if p.exists() else 0.0


# ---------- copia esencial preparada en C: (job semanal) ----------
def run() -> str:
    cfg = load_config()["essential"]
    excludes = load_config()["excludes"]
    snapshot_db()
    ensure_repo(cfg["repo"])
    msg = _backup(cfg["repo"], cfg["sources"], excludes, cfg["exclude_larger_than"])
    _restic(cfg["repo"], "forget", "--keep-last", str(cfg["keep_local_snapshots"]), "--prune", timeout=3600)
    gb = repo_gb(cfg["repo"])
    over = f" | AVISO: pesa {gb:.2f} GB (tope {cfg['max_gb']} GB)" if gb > cfg["max_gb"] else f" | {gb:.2f} GB"
    return msg + over + " | preparada en C: (aún no copiada al disco externo)" + offsite()


def offsite() -> str:
    """Copia cifrada de la copia esencial a la carpeta de OneDrive (si está configurada). Nunca hace fallar la copia local."""
    cfg = load_config()["essential"]
    repo = cfg.get("offsite_repo")
    if not repo:
        return ""
    try:
        Path(repo).parent.mkdir(parents=True, exist_ok=True)
        ensure_repo(cfg["repo"])
        ensure_repo(repo, chunker_from=cfg["repo"])
        c = _restic(repo, "copy", "--from-repo", cfg["repo"], timeout=3600)
        if c.returncode != 0:
            raise RuntimeError(_err(c))
        _restic(repo, "forget", "--keep-last", "8", "--prune", timeout=1800)
        with db.session() as con:
            con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('offsite_last_ok', datetime('now','localtime'))")
        return " | copia cifrada en OneDrive ✔"
    except Exception as e:
        from . import notify
        notify.alert("IMPORTANT", "La copia cifrada en OneDrive falló", str(e)[:200], key=f"offsite-failed:{date.today()}", link="/system")
        return f" | OneDrive FALLÓ: {str(e)[:100]}"


def restore_test(repo: str | None = None) -> str:
    """Verifica integridad (2% de los datos) y restaura un archivo real del último snapshot."""
    repo = repo or load_config()["essential"]["repo"]
    chk = _restic(repo, "check", "--read-data-subset=2%")
    if chk.returncode != 0:
        raise RuntimeError("restic check falló: " + _err(chk))
    ls = _restic(repo, "ls", "latest", "--json")
    files = [json.loads(l) for l in ls.stdout.splitlines() if l.startswith("{")]
    files = [f for f in files if f.get("type") == "file" and 0 < f.get("size", 0) < 5_000_000 and len(f["path"]) < 180]
    if not files:
        raise RuntimeError("snapshot sin archivos pequeños para probar")
    base = db.DATA_DIR / "restore-test"
    base.mkdir(parents=True, exist_ok=True)
    last = ""
    for _ in range(6):  # algunos archivos antiguos tienen nombres/rutas problemáticos en Windows: se prueba otro
        pick = secrets.choice(files)
        tmp = Path(tempfile.mkdtemp(dir=base))  # carpeta nueva por intento: nada de restos de intentos previos
        # restic sale con error si Windows no deja fijar la fecha de carpetas padre (C\Users): se valida el archivo en sí
        r = _restic(repo, "restore", "latest", "--target", str(tmp), "--include", pick["path"])
        if last == "":
            last = f"{pick['path']} -> {r.returncode}: {_err(r)}"
        restored = next((p for p in tmp.rglob("*") if p.is_file() and p.name == Path(pick["path"]).name), None)
        if restored is not None and restored.stat().st_size == pick["size"]:
            shutil.rmtree(base, ignore_errors=True)
            return f"integridad ok; restaurado {Path(pick['path']).name} ({pick['size']} B)"
    shutil.rmtree(base, ignore_errors=True)
    raise RuntimeError("no se pudo restaurar ninguno de 6 archivos de prueba; primer error: " + last)


# ---------- disco externo ----------
def _candidate_drives() -> list[dict]:
    out = []
    for p in psutil.disk_partitions(all=False):
        if p.mountpoint.upper().startswith("C:") or not os.path.exists(p.mountpoint):
            continue
        try:
            u = psutil.disk_usage(p.mountpoint)
        except OSError:
            continue
        out.append({"mount": p.mountpoint, "total_gb": round(u.total / 1e9), "free_gb": round(u.free / 1e9)})
    return out


def find_external(interactive: bool = False) -> Path:
    """Devuelve la raíz del disco externo marcado; si es la primera vez y hay usuario, lo pregunta y lo marca."""
    cands = _candidate_drives()
    for c in cands:
        if (Path(c["mount"]) / MARKER).exists():
            return Path(c["mount"])
    if not cands:
        raise RuntimeError("no hay disco externo conectado")
    if not interactive:
        raise RuntimeError("disco externo no reconocido (ejecuta Backup-Esencial.cmd la primera vez)")
    typer.echo("Primera vez: elige el disco EXTERNO donde se guardarán los backups.")
    for i, c in enumerate(cands, 1):
        typer.echo(f"  {i}) {c['mount']}  total {c['total_gb']} GB, libres {c['free_gb']} GB")
    n = typer.prompt("Número del disco", type=int)
    if not 1 <= n <= len(cands):
        raise RuntimeError("selección inválida")
    root = Path(cands[n - 1]["mount"])
    ans = typer.prompt(f"¿Usar {root} para los backups? Se creará la carpeta PERSONAL_OS_BACKUP. Escribe S para sí o N para no", default="s")
    if ans.strip().lower() not in ("s", "si", "sí", "y", "yes"):
        raise RuntimeError("cancelado por el usuario")
    (root / MARKER).write_text("PERSONAL_OS backup disk\n", encoding="utf-8")
    return root


def _close_plan_task(mode: str) -> None:
    with db.session() as con:  # cierra la tarea recurrente del plan (y programa la siguiente)
        row = con.execute("SELECT id FROM tasks WHERE title=? AND status!='done' ORDER BY id LIMIT 1", (TASKS[mode],)).fetchone()
        if row:
            db.complete_task(con, row["id"])


def run_external(mode: str, live: bool = False) -> str:
    cfg = load_config()
    ext, ess = cfg["external"], cfg["essential"]
    root = find_external(False)
    snapshot_db()
    free_gb = psutil.disk_usage(str(root)).free / 1e9
    if free_gb < ext["min_free_gb"]:
        raise RuntimeError(f"poco espacio libre en {root}: {free_gb:.0f} GB")
    if mode == "essential":
        repo, local = str(root / ext["essential_repo_dir"]), ess["repo"]
        ensure_repo(local)
        ensure_repo(repo, chunker_from=local)
        _backup(local, ess["sources"], cfg["excludes"], ess["exclude_larger_than"], live=live)  # copia lista, fresca
        c = _restic(repo, "copy", "--from-repo", local, live=live)  # "pegar" en el disco
        if c.returncode != 0:
            raise RuntimeError("restic copy: " + (_err(c) if not live else f"código {c.returncode}"))
        _restic(repo, "forget", "--keep-last", "12", "--keep-monthly", "24", "--prune", timeout=3600, live=live)
        verify = restore_test(repo)
        # "cortar": solo tras verificar, lo local se recorta al mínimo para que no se acumule en C:
        _restic(local, "forget", "--keep-last", str(ess["keep_local_snapshots"]), "--prune", timeout=3600, live=live)
        msg = f"esencial copiado ({repo_gb(repo):.2f} GB en el disco)"
    elif mode == "full":
        repo = str(root / ext["full_repo_dir"])
        ensure_repo(repo)
        msg = _backup(repo, ext["full_sources"], cfg["excludes"], None, live=live)  # directo, incremental
        _restic(repo, "forget", "--keep-last", "3", "--keep-monthly", "6", "--prune", timeout=4 * 3600, live=live)
        verify = restore_test(repo)
    else:
        raise ValueError("modo: essential | full")
    _close_plan_task(mode)
    return f"{msg} | {verify} | destino {root}"


@app.command("external")
def external_cmd(mode: str = typer.Argument(..., help="essential | full"),
                 interactive: bool = typer.Option(False, "--interactive", help="Pregunta el disco la primera vez y muestra progreso")) -> None:
    """Copia al disco externo (doble clic: scripts\\Backup-Esencial.cmd / Backup-Completo.cmd)."""
    from . import jobs
    if mode not in ("essential", "full"):
        raise typer.BadParameter("usa essential o full")
    try:
        find_external(interactive)
    except RuntimeError as e:
        typer.echo(f"No se pudo iniciar: {e}")
        raise typer.Exit(1)
    if mode == "full":
        typer.echo("COMPLETO: copia todo el PC. La primera vez tarda horas (después es incremental). No desconectes el disco.")
    else:
        typer.echo("ESENCIAL: pocos minutos. No desconectes el disco.")
    if interactive:
        os.environ["POS_LIVE"] = "1"  # muestra el progreso de restic en la ventana
    ok, msg = jobs.run_job(f"backup-{mode}")
    typer.echo(("OK: " if ok else "FALLÓ: ") + msg)
    raise typer.Exit(0 if ok else 1)


@app.command("run")
def run_cmd() -> None:
    """Prepara la copia esencial en C: (el job semanal hace lo mismo)."""
    from . import jobs
    ok, msg = jobs.run_job("backup")
    typer.echo(("OK " if ok else "FALLÓ ") + msg)


@app.command("test")
def test_cmd() -> None:
    from . import jobs
    ok, msg = jobs.run_job("restore-test")
    typer.echo(("OK " if ok else "FALLÓ ") + msg)


@app.command("status")
def status() -> None:
    cfg = load_config()
    typer.echo(f"copia esencial preparada: {cfg['essential']['repo']}  ({repo_gb(cfg['essential']['repo']):.2f} GB)")
    typer.echo((_restic(cfg["essential"]["repo"], "snapshots", "--compact", timeout=120).stdout or "")[-1000:])
    try:
        typer.echo(f"disco externo: {find_external(False)}")
    except RuntimeError as e:
        typer.echo(f"disco externo: {e}")


@app.command("key")
def key() -> None:
    """Muestra la contraseña de los repositorios para guardarla en Bitwarden. Sin ella no hay restauración."""
    with db.session() as con:
        db.audit(con, "backup.key.show", "SENSITIVE_ACTION", "restic-password")
    typer.echo(_password())
