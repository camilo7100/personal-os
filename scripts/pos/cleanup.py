"""Job `cleanup`: hace cumplir la exigencia de orden. Borra solo lo regenerable o vencido; lo dudoso lo reporta.
Procedimiento humano equivalente: SYSTEM_CLEANUP.md."""
import re
import shutil
import time
from pathlib import Path

from . import db, notify

ROOT_ALLOWED_FILES = {
    "README.md", "ARCHITECTURE.md", "SYSTEM_MAP.md", "DICTIONARY.md", "TOOLS.md", "JOBS.md", "SKILLS.md", "MCP.md",
    "AGENTS.md", "DATA_ARCHITECTURE.md", "SECURITY.md", "BACKUP.md", "RECOVERY.md", "DECISIONS.md", "CHANGELOG.md",
    "ROADMAP.md", "MANUAL.md", "CLAUDE.md", "SYSTEM_CLEANUP.md", ".gitignore",
}
SKIP_DIRS = {".git", "data", "archive", "__pycache__", ".pytest_cache"}
BAD_NAME = re.compile(r"(^|[_\-. ])(old|final|new|copy|copia|bak|tmp|temp|test2|maybe|unused|deprecated)([_\-. ]|$)|_v\d+\b|\(\d+\)", re.I)
REGENERABLE = ("__pycache__", ".pytest_cache")
RETENTION_SQL = (
    "DELETE FROM health WHERE ts < datetime('now','-90 day')",
    "DELETE FROM health WHERE metric NOT IN (SELECT metric FROM health WHERE ts >= datetime('now','-7 day'))",  # métricas obsoletas
    "DELETE FROM job_runs WHERE started_at < datetime('now','-180 day')",
    "DELETE FROM alerts WHERE acknowledged=1 AND ts < datetime('now','-90 day')",
    "DELETE FROM emails WHERE reviewed=1 AND category IN ('promo','newsletter','other') AND ingested_at < datetime('now','-90 day')",
    "DELETE FROM audit_log WHERE ts < datetime('now','-400 day')",
)


def _walk(root: Path):
    for p in root.rglob("*"):
        if not any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            yield p


def run() -> str:
    root, done, findings = db.ROOT, [], []

    # 1) Borrar lo regenerable / temporal
    for p in list(root.rglob("*")):
        if p.name in REGENERABLE and ".git" not in p.parts and p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
    shutil.rmtree(db.DATA_DIR / "restore-test", ignore_errors=True)
    logs = db.DATA_DIR / "logs"
    if logs.exists():
        old = [f for f in logs.glob("*.log") if time.time() - f.stat().st_mtime > 60 * 86400]
        for f in old:
            f.unlink()
        if old:
            done.append(f"{len(old)} logs viejos")

    # 2) Retención de datos + compactación
    with db.session() as con:
        removed = sum(con.execute(q).rowcount for q in RETENTION_SQL)
        db.audit(con, "cleanup.retention", "WRITE", "pos.db", detail=f"{removed} filas vencidas", actor="job")
    if removed:
        done.append(f"{removed} filas vencidas")
    c = db.connect()
    c.execute("VACUUM")
    c.close()

    # 3) Carpetas vacías dentro del proyecto (salvo las estructurales)
    for d in sorted((p for p in _walk(root) if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if d.name not in ("archive", "experiments") and not any(d.iterdir()):
            d.rmdir()
            done.append(f"carpeta vacía {d.relative_to(root)}")

    # 4) Hallazgos que requieren decisión (no se borran solos)
    for p in root.iterdir():
        if p.is_file() and p.name not in ROOT_ALLOWED_FILES:
            findings.append(f"suelto en la raíz: {p.name}")
    for p in _walk(root):
        if BAD_NAME.search(p.stem if p.is_file() else p.name):
            findings.append(f"nombre sospechoso: {p.relative_to(root)}")
    arch = [p.name for p in root.glob("ARCHITECTURE*.md")]
    if len(arch) > 1:
        findings.append(f"documentos de arquitectura duplicados: {arch}")
    findings += _orphan_backup_repos()

    msg = f"borrado: {', '.join(done) or 'nada'} | hallazgos: {len(findings)}"
    if findings:
        notify.alert("IMPORTANT", f"cleanup: {len(findings)} hallazgos de orden", "; ".join(findings)[:450])
        msg += " -> " + "; ".join(findings)[:300]
    return msg


def _orphan_backup_repos() -> list[str]:
    """Carpetas dentro de PERSONAL_OS_BACKUP que ya no aparecen en la configuración de backup."""
    from . import backup
    cfg = backup.load_config()
    base = Path(cfg["essential"]["repo"]).parent
    known = {Path(cfg["essential"]["repo"]).name}
    return [f"repo de backup huérfano: {d}" for d in base.iterdir() if d.is_dir() and d.name not in known] if base.exists() else []
