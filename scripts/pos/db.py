"""Acceso a SQLite: conexión, esquema y auditoría. Única puerta de escritura del sistema."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(os.environ.get("POS_HOME", Path(__file__).resolve().parents[2]))
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.environ.get("POS_DB", DATA_DIR / "pos.db"))
SCHEMA_VERSION = "1"
# El Programador de tareas de Windows NO siempre define APPDATA/LOCALAPPDATA: se derivan del perfil del usuario (siempre disponible).
HOME_DIR = Path.home()
APPDATA = Path(os.environ.get("APPDATA") or HOME_DIR / "AppData" / "Roaming")
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA") or HOME_DIR / "AppData" / "Local")


_ready: set[str] = set()


def connect() -> sqlite3.Connection:
    """Abre la base; la primera vez en cada proceso aplica esquema y migraciones (CLI, jobs y portal se autorreparan)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    if str(DB_PATH) not in _ready:
        _ready.add(str(DB_PATH))  # antes de migrar, para no recursar
        try:
            _apply_schema(con)
            con.commit()
        except Exception:
            _ready.discard(str(DB_PATH))
            raise
    return con


@contextmanager
def session():
    con = connect()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _add_column(con, table: str, col: str, ddl: str) -> None:
    if col not in {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}:
        try:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        except sqlite3.OperationalError as e:  # otro proceso la añadió a la vez
            if "duplicate column" not in str(e):
                raise


def _apply_schema(con) -> None:
    """Esquema idempotente + migraciones de bases creadas por versiones anteriores."""
    con.executescript((Path(__file__).parent / "schema.sql").read_text(encoding="utf-8"))
    _add_column(con, "tasks", "recurrence", "TEXT CHECK (recurrence IN ('daily','weekly','biweekly','monthly','quarterly','yearly'))")
    for col, ddl in (("key", "TEXT"), ("source", "TEXT NOT NULL DEFAULT 'system'"), ("pushed", "INTEGER NOT NULL DEFAULT 0"), ("link", "TEXT")):
        _add_column(con, "alerts", col, ddl)
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_key ON alerts(key) WHERE key IS NOT NULL")
    for table in ("tasks", "projects"):
        _add_column(con, table, "tags", "TEXT")
    _add_column(con, "inbox", "suggestion", "TEXT")
    _add_column(con, "people", "circle", "TEXT")
    _add_column(con, "habits", "project_id", "INTEGER")
    _add_column(con, "transactions", "manual_category", "INTEGER NOT NULL DEFAULT 0")
    _add_column(con, "balances", "estimated", "INTEGER NOT NULL DEFAULT 0")  # 1 = saldo supuesto mientras llega el extracto (se borra solo al llegar)
    for col, ddl in (("start_time", "TEXT"), ("est_minutes", "INTEGER"), ("parent_id", "INTEGER"), ("someday", "INTEGER NOT NULL DEFAULT 0"),
                     ("actual_minutes", "INTEGER NOT NULL DEFAULT 0")):
        _add_column(con, "tasks", col, ddl)
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))


def init_db() -> None:
    with session():  # abrir la conexión ya aplica el esquema
        pass


REPEAT_DAYS = {"daily": 1, "weekly": 7, "biweekly": 14}


def _next_due(current: str, recurrence: str) -> str:
    from datetime import date, timedelta
    d = date.fromisoformat(current)
    if recurrence in REPEAT_DAYS:
        return (d + timedelta(days=REPEAT_DAYS[recurrence])).isoformat()
    months = {"monthly": 1, "quarterly": 3, "yearly": 12}[recurrence]
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):  # ajusta fin de mes (31 -> 30/28)
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            continue
    raise ValueError("fecha inválida")


def complete_task(con, task_id: int) -> int | None:
    """Marca la tarea como hecha; si es recurrente crea la siguiente ocurrencia. Devuelve el id de la nueva."""
    row = con.execute("SELECT * FROM tasks WHERE id=? AND status!='done'", (task_id,)).fetchone()
    if not row:
        return None
    con.execute("UPDATE tasks SET status='done', done_at=datetime('now') WHERE id=?", (task_id,))
    if not row["recurrence"]:
        return None
    d = __import__("datetime")
    base = row["due_date"] or row["scheduled_date"] or d.date.today().isoformat()
    nxt = _next_due(base, row["recurrence"])
    sched = None
    if row["scheduled_date"]:  # conserva la distancia entre «cuándo la trabajo» y «cuándo vence»
        gap = (d.date.fromisoformat(base) - d.date.fromisoformat(row["scheduled_date"])).days if row["due_date"] else 0
        sched = (d.date.fromisoformat(nxt) - d.timedelta(days=gap)).isoformat()
    cur = con.execute(
        "INSERT INTO tasks(title, notes, priority, project_id, entity, due_date, scheduled_date, source, recurrence, tags, start_time, est_minutes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (row["title"], row["notes"], row["priority"], row["project_id"], row["entity"], nxt, sched, "recurrence", row["recurrence"],
         row["tags"], row["start_time"], row["est_minutes"]))
    return cur.lastrowid


def audit(con, action: str, permission: str = "WRITE", target: str = "", result: str = "ok",
          detail: str = "", actor: str | None = None) -> None:
    actor = actor or os.environ.get("POS_ACTOR", "user")
    con.execute(
        "INSERT INTO audit_log(actor, permission, action, target, result, detail) VALUES (?,?,?,?,?,?)",
        (actor, permission, action, target, result, detail[:500]),
    )
