"""Registro y ejecución de jobs (N2). Cada ejecución queda en job_runs; los fallos generan alerta."""
import json
import os
import shutil
import subprocess
import traceback
import urllib.request
from datetime import datetime

import psutil
import typer

from . import db, notify

app = typer.Typer(help="Jobs automáticos", no_args_is_help=True)
REGISTRY: dict = {}


def job(name: str):
    def deco(fn):
        REGISTRY[name] = fn
        return fn
    return deco


def _metric(con, metric: str, value: float | None, status: str = "ok", detail: str = "") -> None:
    con.execute("INSERT INTO health(metric, value, status, detail) VALUES (?,?,?,?)", (metric, value, status, detail))


@job("health")
def health_job() -> str:
    """Recolecta métricas del sistema en la tabla health (base de la sección SISTEMA del portal)."""
    with db.session() as con:
        free_gb = shutil.disk_usage("C:\\").free / 1e9
        _metric(con, "disk_free_gb", round(free_gb, 1), "crit" if free_gb < 20 else "warn" if free_gb < 50 else "ok")
        ram = psutil.virtual_memory().percent
        _metric(con, "ram_used_pct", ram, "warn" if ram > 90 else "ok")
        _metric(con, "cpu_pct", psutil.cpu_percent(interval=1))
        try:
            urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=3).read()
            _metric(con, "ollama_up", 1)
        except Exception:
            _metric(con, "ollama_up", 0, "warn", "Ollama no responde")
        for name, job_name, warn, crit in (("backup_prepared_age_days", "backup", 8, 15),
                                           ("backup_essential_ext_age_days", "backup-essential", 35, 70),
                                           ("backup_full_ext_age_days", "backup-full", 120, 200)):
            row = con.execute("SELECT MAX(finished_at) FROM job_runs WHERE job=? AND status='ok'", (job_name,)).fetchone()[0]
            if row:
                age = (datetime.now() - datetime.fromisoformat(row)).days
                _metric(con, name, age, "crit" if age > crit else "warn" if age > warn else "ok")
            else:
                _metric(con, name, None, "warn", "nunca ejecutado")
        off = con.execute("SELECT value FROM meta WHERE key='offsite_last_ok'").fetchone()
        try:
            from . import backup
            if backup.load_config()["essential"].get("offsite_repo"):
                if off:
                    age = (datetime.now() - datetime.fromisoformat(off["value"])).days
                    _metric(con, "backup_offsite_age_days", age, "crit" if age > 21 else "warn" if age > 10 else "ok")
                else:
                    _metric(con, "backup_offsite_age_days", None, "warn", "aún sin copia en OneDrive")
        except Exception:
            pass
        try:
            from . import backup
            cfg = backup.load_config()["essential"]
            gb = backup.repo_gb(cfg["repo"])
            _metric(con, "backup_essential_gb", round(gb, 2), "warn" if gb > cfg["max_gb"] else "ok",
                    f"tope {cfg['max_gb']} GB")
        except Exception:
            pass
        bad = con.execute("SELECT COUNT(*) FROM health WHERE ts >= datetime('now','-1 minute') AND status!='ok'").fetchone()[0]
    return f"free={free_gb:.0f}GB ram={ram}% alertas={bad}"


@job("mail-ingest")
def mail_ingest_job() -> str:
    from . import mail
    return mail.ingest()


@job("backup")
def backup_job() -> str:
    from . import backup
    return backup.run()


@job("briefing")
def briefing_job() -> str:
    from . import briefing
    return briefing.write("morning")


@job("evening-review")
def evening_job() -> str:
    from . import briefing
    return briefing.write("evening")


@job("weekly-review")
def weekly_job() -> str:
    from . import briefing
    return briefing.write("weekly")


@job("repos-scan")
def repos_job() -> str:
    from . import repos
    return repos.scan()


@job("notes-index")
def notes_job() -> str:
    from . import notes
    return notes.index_notes()


@job("inbox-triage")
def triage_job() -> str:
    from . import triage
    return triage.run()


@job("statements")
def statements_job() -> str:
    from . import statements
    return statements.run_job()


@job("panel")
def panel_job() -> str:
    from . import panel
    return panel.run_job()


@job("telegram-capture")
def telegram_job() -> str:
    from . import telegram
    return telegram.capture()


@job("media-watch")
def media_job() -> str:
    from . import media
    return media.watch()


@job("reminders")
def reminders_job() -> str:
    from . import reminders
    return reminders.run()


@job("cleanup")
def cleanup_job() -> str:
    from . import cleanup
    return cleanup.run()


@job("backup-essential")
def backup_essential_job() -> str:
    from . import backup
    return backup.run_external("essential", live=os.environ.get("POS_LIVE") == "1")


@job("backup-full")
def backup_full_job() -> str:
    from . import backup
    return backup.run_external("full", live=os.environ.get("POS_LIVE") == "1")


@job("restore-test")
def restore_test_job() -> str:
    from . import backup
    return backup.restore_test()


def run_job(name: str) -> tuple[bool, str]:
    fn = REGISTRY[name]
    started = datetime.now().isoformat(timespec="seconds")
    with db.session() as con:
        run_id = con.execute("INSERT INTO job_runs(job, started_at) VALUES (?,?)", (name, started)).lastrowid
    try:
        msg, ok = fn(), True
    except Exception as e:  # el wrapper nunca deja un fallo silencioso
        msg, ok = f"{type(e).__name__}: {e}", False
        (db.DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)
        (db.DATA_DIR / "logs" / f"{name}.log").open("a", encoding="utf-8").write(
            f"[{started}]\n{traceback.format_exc()}\n")
    with db.session() as con:
        con.execute("UPDATE job_runs SET finished_at=?, status=?, message=? WHERE id=?",
                    (datetime.now().isoformat(timespec="seconds"), "ok" if ok else "failed", str(msg)[:500], run_id))
        db.audit(con, f"job.{name}", "LOCAL_ACTION", name, "ok" if ok else "failed", str(msg), actor="job")
    if not ok:
        notify.alert("IMPORTANT", f"Job fallido: {name}", str(msg), key=f"job-failed:{name}:{datetime.now():%Y-%m-%d}", link="/jobs")
    return ok, str(msg)


@app.command("list")
def list_jobs(as_json: bool = typer.Option(False, "--json")) -> None:
    with db.session() as con:
        rows = []
        for name in sorted(REGISTRY):
            r = con.execute("SELECT started_at, status, message FROM job_runs WHERE job=? ORDER BY id DESC LIMIT 1", (name,)).fetchone()
            rows.append({"job": name, "last_run": r["started_at"] if r else None, "status": r["status"] if r else "never",
                         "message": r["message"] if r else ""})
    if as_json:
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            typer.echo(f"{r['job']:<14} {r['status']:<8} {r['last_run'] or '-'}  {r['message'] or ''}")


@app.command("run")
def run(name: str) -> None:
    if name not in REGISTRY:
        raise typer.BadParameter(f"job desconocido: {name}. Disponibles: {', '.join(sorted(REGISTRY))}")
    ok, msg = run_job(name)
    typer.echo(("OK " if ok else "FALLÓ ") + msg)
    raise typer.Exit(0 if ok else 1)
