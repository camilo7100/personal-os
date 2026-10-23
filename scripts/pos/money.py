"""Finanzas por importación de extractos CSV (sin APIs bancarias). Personal y negocio separados por `entity`."""
import csv
import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path

import typer

from . import db
from .util import JSON, emit

app = typer.Typer(help="Finanzas (importación de extractos)", no_args_is_help=True)
RULES_FILE = db.ROOT / "config" / "money_rules.json"
NOT_TRANSFER = "COALESCE(t.category,'') NOT IN ('Movimiento entre mis cuentas','Pago de tarjeta de crédito','Por aclarar')"  # lo interno (y lo que falta aclarar) no es ingreso ni gasto
WORK_INCOME = ("Salario",)  # lo único que cuenta como ingreso: tu trabajo (nómina). Rendimientos, familia, cashback, etc. se muestran aparte
OFFSETS = ("Devoluciones y reembolsos", "Cashback")  # restan del gasto (te devolvieron plata que gastaste)
_IN = lambda cats: "(" + ",".join("'" + c + "'" for c in cats) + ")"
ASSUME_FILE = db.ROOT / "config" / "finance.json"
ASSUME_DEFAULTS = {"assumed_income": 7000000, "assume_for": ["Bancolombia"],
                   "lag_for": ["Bancolombia", "Fiducuenta", "Bolsillo"],  # cuentas cuyo extracto siempre llega tarde: no bloquean el mes y se estiman mes a mes
                   "owner_name": "nombre apellido titular"}  # titular de tus cuentas (un extracto a otro nombre es de un tercero)  # mientras falte el extracto de esa cuenta, se supone este ingreso mensual por trabajo
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%y", "%m/%d/%Y")


def parse_amount(raw: str) -> float:
    """Entiende 1.234,56 · 1,234.56 · 12.500 (miles) · -3500 · (3500) · $ 1.000."""
    s = re.sub(r"[^\d,.\-()]", "", str(raw).strip())
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-") or s.endswith("-")
    s = s.strip("()-")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if re.search(r",\d{1,2}$", s) else s.replace(",", "")
    elif "." in s and re.search(r"\.\d{3}(\.|$)", s):
        s = s.replace(".", "")
    val = float(s or 0)
    return -val if neg else val


def parse_date(raw: str, fmt: str | None = None) -> str:
    raw = raw.strip()
    for f in ([fmt] if fmt else DATE_FORMATS):
        try:
            return datetime.strptime(raw, f).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"fecha no reconocida: {raw}")


def assume_config() -> dict:
    try:
        return {**ASSUME_DEFAULTS, **json.loads(ASSUME_FILE.read_text(encoding="utf-8"))}
    except Exception:
        return dict(ASSUME_DEFAULTS)


def is_assumed_account(name: str) -> bool:
    return any(name.startswith(p) for p in assume_config()["assume_for"])


def is_lag_account(name: str) -> bool:
    return any(name.startswith(p) for p in assume_config()["lag_for"])


def assumed_income(con, month: str, today: date | None = None) -> float | None:
    """Ingreso supuesto del mes: aplica si la cuenta donde cae la nómina aún no tiene extracto de ese mes (y ya tiene alguno, o sea que es un atraso
    y no una cuenta nueva). En cuanto llega el extracto, deja de aplicar y cuenta el ingreso real."""
    from . import statements
    today = today or date.today()
    if month >= today.strftime("%Y-%m"):  # el mes en curso aún no tiene nómina completa: no se supone nada
        return None
    amount = float(assume_config()["assumed_income"] or 0)
    if amount <= 0:
        return None
    accs = [a for a in con.execute("SELECT id, name FROM accounts") if is_assumed_account(a["name"])]
    def late_for(a) -> bool:  # solo aplica DESPUÉS del primer mes que esa cuenta sí cubre (antes no hay historia que suponer) y mientras falte su extracto
        firsts = [r["start"][:7] for r in con.execute("SELECT start FROM statement_periods WHERE account_id=? ORDER BY start", (a["id"],))]
        if not firsts:
            return False
        y, m = int(firsts[0][:4]), int(firsts[0][5:7])
        first_cov = None
        while f"{y:04d}-{m:02d}" <= month:
            if statements.covered(con, a["id"], f"{y:04d}-{m:02d}"):
                first_cov = f"{y:04d}-{m:02d}"
                break
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return first_cov is not None and month > first_cov and not statements.covered(con, a["id"], month)
    late = [a for a in accs if late_for(a)]
    return amount if late else None


def load_rules() -> dict:
    try:
        return json.loads(RULES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def categorize(description: str, rules: dict) -> str | None:
    d = description.lower()
    for cat, words in rules.items():
        if any(w.lower() in d for w in words):
            return cat
    return "Salario" if re.search(r"n[oó]mina|salario", d) else None  # tu sueldo siempre se reconoce, aunque no tengas reglas propias


@app.command("account")
def account_add(name: str, kind: str = "bank", entity: str = "personal", currency: str = "COP") -> None:
    with db.session() as con:
        con.execute("INSERT OR IGNORE INTO accounts(name, kind, entity, currency) VALUES (?,?,?,?)", (name, kind, entity, currency))
        db.audit(con, "money.account", "WRITE", name)
    typer.echo("ok")


def import_file(con, file: Path, account: str, date_col: str = "fecha", desc_col: str = "descripcion",
                amount_col: str | None = None, debit_col: str | None = None, credit_col: str | None = None,
                delimiter: str = ";", date_format: str | None = None, encoding: str = "utf-8-sig") -> tuple[int, int]:
    """Importa un extracto CSV. Reimportar el mismo archivo no duplica movimientos. Devuelve (nuevos, repetidos)."""
    rules = load_rules()
    added = skipped = 0
    acc = con.execute("SELECT id FROM accounts WHERE name=?", (account,)).fetchone()
    if not acc:
        raise ValueError(f"cuenta inexistente: {account} (créala primero)")
    seen: dict[str, int] = {}
    with open(file, newline="", encoding=encoding) as fh:
        for row in csv.DictReader(fh, delimiter=delimiter):
            row = {(k or "").strip().lower(): (v or "") for k, v in row.items()}
            d = parse_date(row[date_col.lower()], date_format)
            desc = row[desc_col.lower()].strip()
            if amount_col:
                amt = parse_amount(row[amount_col.lower()])
            else:
                amt = parse_amount(row.get((credit_col or "").lower(), "0") or "0") - abs(parse_amount(row.get((debit_col or "").lower(), "0") or "0"))
            key = f"{acc['id']}|{d}|{desc}|{amt}"
            seen[key] = seen.get(key, 0) + 1
            h = hashlib.sha1(f"{key}|{seen[key]}".encode()).hexdigest()
            cur = con.execute(
                "INSERT OR IGNORE INTO transactions(account_id, date, description, amount, category, hash) VALUES (?,?,?,?,?,?)",
                (acc["id"], d, desc, amt, categorize(desc, rules), h))
            added += cur.rowcount
            skipped += 1 - cur.rowcount
    db.audit(con, "money.import", "WRITE", account, detail=f"{added} nuevos, {skipped} repetidos")
    return added, skipped


@app.command("import")
def import_csv(file: Path, account: str, date_col: str = "fecha", desc_col: str = "descripcion",
               amount_col: str = typer.Option(None, help="Columna con monto firmado"),
               debit_col: str = typer.Option(None, help="Columna de débitos (gastos)"),
               credit_col: str = typer.Option(None, help="Columna de créditos (ingresos)"),
               delimiter: str = ";", date_format: str = typer.Option(None), encoding: str = "utf-8-sig") -> None:
    """Importa un extracto CSV. Reimportar el mismo archivo no duplica movimientos."""
    try:
        with db.session() as con:
            added, skipped = import_file(con, file, account, date_col, desc_col, amount_col, debit_col, credit_col, delimiter, date_format, encoding)
    except ValueError as e:
        raise typer.BadParameter(str(e))
    typer.echo(f"{added} movimientos nuevos, {skipped} ya existían")


def month_summary(con, month: str | None = None, entity: str | None = None) -> dict:
    month = month or date.today().strftime("%Y-%m")
    ent = "AND a.entity=?" if entity else ""
    args = (month, entity) if entity else (month,)
    base = f"FROM transactions t JOIN accounts a ON a.id=t.account_id WHERE substr(t.date,1,7)=? {ent} AND {NOT_TRANSFER}"
    income = con.execute(f"SELECT COALESCE(SUM(amount),0) {base} AND amount>0 AND t.category IN {_IN(WORK_INCOME)}", args).fetchone()[0]
    refunds = con.execute(f"SELECT COALESCE(SUM(amount),0) {base} AND amount>0 AND t.category IN {_IN(OFFSETS)}", args).fetchone()[0]
    other_in = [dict(r) for r in con.execute(
        f"SELECT COALESCE(t.category,'sin categoría') AS category, ROUND(SUM(amount),0) AS total {base} AND amount>0 "
        f"AND COALESCE(t.category,'') NOT IN {_IN(WORK_INCOME + OFFSETS)} GROUP BY 1 ORDER BY total DESC", args)]
    expense = con.execute(f"SELECT COALESCE(SUM(amount),0) {base} AND amount<0", args).fetchone()[0] + refunds  # gasto neto de devoluciones
    cats = [dict(r) for r in con.execute(
        f"SELECT COALESCE(category,'sin categoría') AS category, ROUND(SUM(amount),2) AS total {base} AND amount<0 "
        f"GROUP BY 1 ORDER BY total", args)]
    budgets = {r["category"]: r["monthly_amount"] for r in con.execute("SELECT * FROM budgets")}
    for c in cats:
        c["budget"] = budgets.get(c["category"])
        c["over_budget"] = bool(c["budget"] and -c["total"] > c["budget"])
    assumed = assumed_income(con, month) if not entity else None
    real_income = income
    if assumed is not None:
        income = assumed
    return {"month": month, "income": round(income, 2), "income_assumed": assumed is not None, "income_real": round(real_income, 2), "expense": round(-expense, 2), "net": round(income + expense, 2),
            "by_category": cats, "other_in": other_in, "other_in_total": round(sum(o["total"] for o in other_in), 0), "refunds": round(refunds, 2), "uncategorized": sum(1 for c in cats if c["category"] == "sin categoría")}


@app.command("summary")
def summary(month: str = typer.Option(None, help="YYYY-MM"), entity: str = typer.Option(None), as_json: bool = JSON) -> None:
    with db.session() as con:
        s = month_summary(con, month, entity)
    if as_json:
        typer.echo(json.dumps(s, ensure_ascii=False, indent=2))
        return
    typer.echo(f"{s['month']}: ingresos {s['income']:,.0f} · gastos {s['expense']:,.0f} · neto {s['net']:,.0f}")
    for c in s["by_category"]:
        flag = "  ⚠ sobre presupuesto" if c["over_budget"] else ""
        typer.echo(f"  {c['category']:<22}{-c['total']:>14,.0f}{flag}")


@app.command("budget")
def budget_set(category: str, monthly_amount: float) -> None:
    with db.session() as con:
        con.execute("INSERT INTO budgets(category, monthly_amount) VALUES (?,?) ON CONFLICT(category) DO UPDATE SET monthly_amount=excluded.monthly_amount",
                    (category, monthly_amount))
        db.audit(con, "money.budget", "WRITE", category)
    typer.echo("ok")


@app.command("list")
def tx_list(limit: int = 30, uncategorized: bool = False, as_json: bool = JSON) -> None:
    with db.session() as con:
        where = "WHERE t.category IS NULL" if uncategorized else ""
        emit(con.execute(
            f"SELECT t.id, t.date, a.name AS account, t.amount, COALESCE(t.category,'') AS category, t.description "
            f"FROM transactions t JOIN accounts a ON a.id=t.account_id {where} ORDER BY t.date DESC, t.id DESC LIMIT ?", (limit,)).fetchall(), as_json)


@app.command("categorize")
def tx_categorize(tx_id: int, category: str) -> None:
    with db.session() as con:
        con.execute("UPDATE transactions SET category=? WHERE id=?", (category, tx_id))
        db.audit(con, "money.categorize", "WRITE", f"tx:{tx_id}", detail=category)
    typer.echo("ok")
