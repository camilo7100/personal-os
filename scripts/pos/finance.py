"""Panel de finanzas: series, recomendaciones y gráficos (SVG generado en el servidor, sin librerías externas).
Inspirado en lo que hacen Actual Budget, Firefly III, Monarch y Copilot: patrimonio en el tiempo, dona por categoría, barras mensuales
ingresos vs gastos, mapa de calor diario, presupuestos con barras de avance y metas de ahorro; más la regla 50/30/20 y el colchón de emergencia."""
import calendar
import csv
import json
import re
from datetime import date, timedelta
from pathlib import Path

import typer

from . import db, money
from .util import JSON

app = typer.Typer(help="Panel de finanzas: saldos, metas de ahorro y recomendaciones", no_args_is_help=True)
MONTHS_ES = ["", "ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
PALETTE = ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8"]


def fmt(n: float) -> str:
    return f"{n:,.0f}".replace(",", ".")


def _month_back(ym: str, k: int) -> str:
    y, m = map(int, ym.split("-"))
    idx = y * 12 + (m - 1) - k  # k negativo avanza meses
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def monthly_series(con, months: int = 12, entity: str | None = None, upto: str | None = None) -> list[dict]:
    upto = upto or date.today().strftime("%Y-%m")
    ent, args = ("AND a.entity=?", (entity,)) if entity else ("", ())
    got = {r["m"]: r for r in con.execute(
        f"SELECT substr(t.date,1,7) m, SUM(CASE WHEN t.amount>0 AND t.category IN {money._IN(money.WORK_INCOME)} THEN t.amount ELSE 0 END) inc, "
        f"SUM(CASE WHEN t.amount<0 THEN -t.amount WHEN t.amount>0 AND t.category IN {money._IN(money.OFFSETS)} THEN -t.amount ELSE 0 END) exp "
        f"FROM transactions t JOIN accounts a ON a.id=t.account_id WHERE 1=1 {ent} AND {money.NOT_TRANSFER} GROUP BY m", args)}
    out = []
    for k in range(months - 1, -1, -1):
        m = _month_back(upto, k)
        r = got.get(m)
        inc = round(r["inc"], 0) if r else 0
        if not entity:
            a = money.assumed_income(con, m)
            inc = a if a is not None else inc
        out.append({"month": m, "label": MONTHS_ES[int(m[5:])], "income": inc, "expense": round(r["exp"], 0) if r else 0})
    return out


# ---------- cuadre de traslados entre tus cuentas ----------
# Cada banco muestra su lado: «TRASLADO A FONDO DE INVERSION» en Bancolombia es el «Adicion» de la Fiducuenta. Un traslado NUNCA es gasto ni ingreso,
# lo veas por un lado o por los dos; el otro extracto puede llegar semanas después, así que aquí se empareja lo que se pueda y se dice qué falta.
COUNTERPART = [  # (patrón en la descripción, cuenta del otro lado)
    (r"fondo de invers|fiducuenta|^adicion$|retiro trasla", "Fiducuenta (inversión)"),
    (r"pse nu |pago tarjeta nu|nu compania", "Nu (crédito)"),
    (r"davivienda|rappicard|pagos por pse|pagos rappipay|pago de rappicard", "RappiCard (crédito)"),
    (r"recarga|rappicuenta", "RappiCuenta (ahorros)"),
]


def _near(a: float, b: float) -> bool:
    return abs(abs(a) - abs(b)) <= max(2000.0, 0.005 * abs(a))


def transfer_links(con, today: date | None = None) -> dict:
    """Empareja traslados de una cuenta con su contraparte (monto parecido, signo contrario, ≤5 días). Lo que queda sin pareja se explica:
    «esperando extracto» si la otra cuenta aún no tiene extracto de esa fecha, o «revisar» si lo tiene y no aparece."""
    rows = [dict(r) for r in con.execute(
        "SELECT t.id, t.date, t.amount, t.description, a.id AS aid, a.name AS account FROM transactions t JOIN accounts a ON a.id=t.account_id "
        "WHERE t.category IN ('Movimiento entre mis cuentas','Pago de tarjeta de crédito') ORDER BY t.date, t.id")]
    used: set[int] = set()
    pairs = []
    for t in rows:
        if t["id"] in used:
            continue
        best = None
        for u in rows:
            if u["id"] in used or u["id"] == t["id"] or u["aid"] == t["aid"] or (u["amount"] > 0) == (t["amount"] > 0) or not _near(t["amount"], u["amount"]):
                continue
            gap = abs((date.fromisoformat(u["date"]) - date.fromisoformat(t["date"])).days)
            if gap <= 5 and (best is None or gap < best[0]):
                best = (gap, u)
        if best:
            used.update((t["id"], best[1]["id"]))
            pairs.append((t, best[1]))
    covered = {}
    for a in con.execute("SELECT id, name FROM accounts"):
        covered[a["name"]] = [(p["start"], p["end"]) for p in con.execute("SELECT start, end FROM statement_periods WHERE account_id=?", (a["id"],))]
    pending, review = [], []
    for t in rows:
        if t["id"] in used:
            continue
        low = t["description"].lower()
        other = next((acc for pat, acc in COUNTERPART if re.search(pat, low) and acc != t["account"]), None)
        if other is None:
            continue
        has = any(s <= t["date"] <= e for s, e in covered.get(other, []))
        item = {**t, "other": other, "direction": "sale" if t["amount"] < 0 else "entra"}
        (review if has else pending).append(item)
    return {"pairs": len(pairs), "pending": pending, "review": review}


def fund_estimate(con) -> dict | None:
    """Saldo estimado de la Fiducuenta: último saldo conocido + traslados vistos en Bancolombia que ocurrieron después del último extracto de la Fiducuenta."""
    acc = con.execute("SELECT id FROM accounts WHERE name LIKE 'Fiducuenta%'").fetchone()
    if not acc:
        return None
    last = con.execute("SELECT MAX(end) e FROM statement_periods WHERE account_id=?", (acc["id"],)).fetchone()["e"]
    bal = con.execute("SELECT amount, date FROM balances WHERE account_id=? ORDER BY date DESC LIMIT 1", (acc["id"],)).fetchone()
    if not last or not bal:
        return None
    moved = con.execute("SELECT COALESCE(SUM(-amount),0) FROM transactions WHERE category='Movimiento entre mis cuentas' AND date>? AND lower(description) LIKE '%fondo de invers%'", (last,)).fetchone()[0]
    return {"since": last, "balance": bal["amount"], "moved": moved, "estimate": bal["amount"] + moved} if moved else None


def year_summary(con, year: int, entity: str | None = None, today: date | None = None) -> dict:
    """Resumen del año (lo que va del año si es el actual): mes por mes, totales, categorías, otras entradas y cambio de patrimonio."""
    today = today or date.today()
    last_m = today.month if year == today.year else (12 if year < today.year else 0)
    first = con.execute("SELECT MIN(substr(date,1,7)) FROM transactions WHERE substr(date,1,4)=?", (str(year),)).fetchone()[0]
    months = [f"{year}-{m:02d}" for m in range(1, last_m + 1) if first and f"{year}-{m:02d}" >= first]
    rows, cats, other = [], {}, {}
    for mo in months:
        s = money.month_summary(con, mo, entity)
        complete = month_complete(con, mo, today)
        rows.append({"month": mo, "label": MONTHS_ES[int(mo[5:])], "income": s["income"], "expense": s["expense"], "net": round(s["income"] - s["expense"], 2),
                     "assumed": s["income_assumed"], "complete": complete})
        for c in s["by_category"]:
            cats[c["category"]] = cats.get(c["category"], 0.0) + -c["total"]
        for o in s["other_in"]:
            other[o["category"]] = other.get(o["category"], 0.0) + o["total"]
    inc, exp = sum(r["income"] for r in rows), sum(r["expense"] for r in rows)
    full = [r for r in rows if r["complete"] and not r["assumed"]]
    rate_base = [r for r in rows if r["complete"]]
    rate = round(100 * (sum(r["income"] for r in rate_base) - sum(r["expense"] for r in rate_base)) / sum(r["income"] for r in rate_base)) if rate_base and sum(r["income"] for r in rate_base) else None
    top = sorted(cats.items(), key=lambda kv: -kv[1])
    total_cat = sum(v for _, v in top) or 1
    nw = [p for p in networth_series(con, entity) if p["date"].startswith(str(year)) or p["date"] < f"{year}-01-01"]
    nw_in_year = [p for p in nw if p["date"].startswith(str(year))]
    before = [p for p in nw if p["date"] < f"{year}-01-01"]
    start_nw = before[-1]["total"] if before else (nw_in_year[0]["total"] if nw_in_year else None)
    end_nw = nw_in_year[-1]["total"] if nw_in_year else None
    best = max(rows, key=lambda r: r["net"]) if rows else None
    worst = min(rows, key=lambda r: r["net"]) if rows else None
    quarters = []
    for q in range(4):
        qm = [r for r in rows if (int(r["month"][5:]) - 1) // 3 == q]
        if qm:
            qi, qe = sum(r["income"] for r in qm), sum(r["expense"] for r in qm)
            quarters.append({"label": f"T{q + 1}", "months": " – ".join([qm[0]["label"], qm[-1]["label"]]) if len(qm) > 1 else qm[0]["label"], "income": round(qi, 0), "expense": round(qe, 0),
                             "net": round(qi - qe, 0), "partial": any(not r["complete"] for r in qm), "assumed": any(r["assumed"] for r in qm)})
    return {"year": year, "quarters": quarters, "months": rows, "income": round(inc, 0), "expense": round(exp, 0), "net": round(inc - exp, 0), "rate": rate,
            "avg_expense": round(sum(r["expense"] for r in rows) / len(rows), 0) if rows else 0, "avg_income": round(inc / len(rows), 0) if rows else 0,
            "categories": [{"name": k, "value": round(v, 0), "pct": round(100 * v / total_cat), "per_month": round(v / max(len(rows), 1), 0)} for k, v in top],
            "other_in": [{"category": k, "total": round(v, 0)} for k, v in sorted(other.items(), key=lambda kv: -kv[1])],
            "assumed": sum(1 for r in rows if r["assumed"]), "partial": sum(1 for r in rows if not r["complete"]), "solid": len(full),
            "networth_start": start_nw, "networth_end": end_nw, "best": best, "worst": worst}


def years_with_data(con) -> list[int]:
    return [int(r[0]) for r in con.execute("SELECT DISTINCT substr(date,1,4) FROM transactions ORDER BY 1 DESC")]


def daily_spend(con, month: str, entity: str | None = None) -> dict[str, float]:
    ent, args = ("AND a.entity=?", (month, entity)) if entity else ("", (month,))
    return {r["d"]: r["s"] for r in con.execute(
        f"SELECT t.date d, SUM(-t.amount) s FROM transactions t JOIN accounts a ON a.id=t.account_id WHERE substr(t.date,1,7)=? AND t.amount<0 {ent} AND {money.NOT_TRANSFER} GROUP BY t.date", args)}


def top_merchants(con, month: str, entity: str | None = None, n: int = 8) -> list[dict]:
    ent, args = ("AND a.entity=?", (month, entity)) if entity else ("", (month,))
    rows = con.execute(f"SELECT t.description d, -t.amount a FROM transactions t JOIN accounts a ON a.id=t.account_id WHERE substr(t.date,1,7)=? AND t.amount<0 {ent} AND {money.NOT_TRANSFER}", args).fetchall()
    agg: dict[str, list] = {}
    for r in rows:
        key = " ".join(re.sub(r"[\d*#/\-_.]+", " ", r["d"]).split()[:3]).title() or "(sin descripción)"
        e = agg.setdefault(key, [0.0, 0])
        e[0] += r["a"]
        e[1] += 1
    return [{"name": k, "total": round(v[0]), "count": v[1]} for k, v in sorted(agg.items(), key=lambda x: -x[1][0])[:n]]


# ---------- saldos, patrimonio y metas ----------
def set_balance(con, account: str, amount: float, day: str | None = None) -> None:
    acc = con.execute("SELECT id FROM accounts WHERE name=?", (account,)).fetchone()
    if not acc:
        raise ValueError(f"cuenta inexistente: {account}")
    con.execute("INSERT INTO balances(account_id, date, amount) VALUES (?,?,?) ON CONFLICT(account_id, date) DO UPDATE SET amount=excluded.amount",
                (acc["id"], day or date.today().isoformat(), amount))
    db.audit(con, "money.balance", "WRITE", account, detail=f"{amount:,.0f}")


def _month_end(ym: str) -> str:
    y, m = map(int, ym.split("-"))
    return ((date(y, m, 1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)).isoformat()


def set_pocket(con, amount: float, day: str | None = None, estimated: bool = False) -> None:
    """Tu bolsillo en la cuenta de tu familiar: el extracto de la RappiCuenta no separa bolsillos, así que su saldo lo das tú (o un estimado) y el sistema lo mueve con el rendimiento."""
    from . import statements
    day = day or date.today().isoformat()
    _account = con.execute("SELECT id FROM accounts WHERE name=?", (statements.POCKET,)).fetchone()
    if not _account:
        con.execute("INSERT INTO accounts(name, kind) VALUES (?, 'savings')", (statements.POCKET,))
        _account = con.execute("SELECT id FROM accounts WHERE name=?", (statements.POCKET,)).fetchone()
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('pocket:base', ?)", (json.dumps({"date": day, "amount": amount, "estimated": estimated}),))
    con.execute("INSERT INTO balances(account_id, date, amount, estimated) VALUES (?,?,?,?) ON CONFLICT(account_id, date) DO UPDATE SET amount=excluded.amount, estimated=excluded.estimated",
                (_account["id"], day, amount, 1 if estimated else 0))
    db.audit(con, "money.pocket", "WRITE", "pocket", detail=f"{amount:,.0f} al {day}{' (estimado)' if estimated else ''}")
    refresh_estimates(con)


def pocket_rate(con) -> float:
    """Rendimiento mensual de la RappiCuenta de tu familiar (intereses netos ÷ saldo inicial de cada extracto que tengo)."""
    ints = opens = 0.0
    for r in con.execute("SELECT value FROM meta WHERE key LIKE 'family:rappicuenta:%'"):
        d = json.loads(r["value"])
        ints += d["interest_net"]
        opens += d["opening"]
    return ints / opens if opens else 0.006


def fund_rate(con) -> float:
    """Rendimiento mensual neto de la Fiducuenta (promedio de los extractos que tengo)."""
    rates = []
    for r in con.execute("SELECT t.date, t.amount FROM transactions t JOIN accounts a ON a.id=t.account_id WHERE a.name LIKE 'Fiducuenta%' AND t.description LIKE 'Rendimientos netos%'"):
        start = r["date"][:7] + "-01"
        b = con.execute("SELECT amount FROM balances b JOIN accounts a ON a.id=b.account_id WHERE a.name LIKE 'Fiducuenta%' AND b.date<? AND b.estimated=0 ORDER BY b.date DESC LIMIT 1", (start,)).fetchone()
        if b and b["amount"] > 0:
            rates.append(r["amount"] / b["amount"])
    return sum(rates) / len(rates) if rates else 0.008


def refresh_estimates(con, today: date | None = None) -> int:
    """Saldos supuestos al cierre de cada mes para las cuentas que siempre se atrasan (Fiducuenta y tu bolsillo): último saldo real × rendimiento mensual
    (+ lo que enviaste a la Fiducuenta según Bancolombia). En cuanto llega el extracto real de ese mes, el supuesto desaparece solo."""
    today = today or date.today()
    last_done = _month_back(today.strftime("%Y-%m"), 1)  # último mes que ya cerró
    n = 0
    for a in con.execute("SELECT id, name FROM accounts WHERE name LIKE 'Fiducuenta%' OR name LIKE 'Bolsillo%'").fetchall():
        con.execute("DELETE FROM balances WHERE account_id=? AND estimated=1", (a["id"],))
        pocket = a["name"].startswith("Bolsillo")
        if pocket:
            base = con.execute("SELECT value FROM meta WHERE key='pocket:base'").fetchone()
            if not base:
                continue
            base = json.loads(base["value"])
            fam = sorted(r["key"][-7:] for r in con.execute("SELECT key FROM meta WHERE key LIKE 'family:rappicuenta:%'"))
            first = _month_back(fam[0], 1) if fam else base["date"][:7]
            r_, cur, cur_m = pocket_rate(con), base["amount"], base["date"][:7]
            months = []
            m = min(first, base["date"][:7])
            while m <= last_done:
                months.append(m)
                m = _month_back(m, -1)
            # hacia atrás desde la base (para tener historia) y hacia adelante
            amounts: dict[str, float] = {cur_m: cur}
            for mm in sorted([x for x in months if x < cur_m], reverse=True):
                nxt = _month_back(mm, -1)
                amounts[mm] = amounts[nxt] / (1 + r_)
            for mm in sorted([x for x in months if x > cur_m]):
                amounts[mm] = amounts[_month_back(mm, 1)] * (1 + r_)
            for mm, amt in amounts.items():
                e = _month_end(mm)
                real = con.execute("SELECT 1 FROM balances WHERE account_id=? AND date=? AND estimated=0", (a["id"], e)).fetchone()
                if real or e == base["date"]:
                    continue
                con.execute("INSERT OR IGNORE INTO balances(account_id, date, amount, estimated) VALUES (?,?,?,1)", (a["id"], e, round(amt, 2)))
                n += 1
            con.execute("INSERT OR REPLACE INTO balances(account_id, date, amount, estimated) VALUES (?,?,?,?)", (a["id"], base["date"], base["amount"], 1 if base.get("estimated") else 0))  # el dato base siempre se conserva
            continue
        real = con.execute("SELECT date, amount FROM balances WHERE account_id=? AND estimated=0 ORDER BY date DESC LIMIT 1", (a["id"],)).fetchone()
        if not real:
            continue
        r_, cur, m = fund_rate(con), real["amount"], _month_back(real["date"][:7], -1)
        while m <= last_done:
            sent = con.execute("SELECT COALESCE(SUM(-amount),0) FROM transactions t JOIN accounts b ON b.id=t.account_id WHERE b.name LIKE 'Bancolombia%' AND substr(t.date,1,7)=? "
                               "AND lower(t.description) LIKE '%fondo de invers%'", (m,)).fetchone()[0]
            cur = cur * (1 + r_) + sent
            con.execute("INSERT OR IGNORE INTO balances(account_id, date, amount, estimated) VALUES (?,?,?,1)", (a["id"], _month_end(m), round(cur, 2)))
            n += 1
            m = _month_back(m, -1)
    return n


def latest_balances(con) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT a.id, a.name, a.kind, a.entity, b.amount, b.date, COALESCE(b.estimated, 0) AS estimated FROM accounts a LEFT JOIN balances b ON b.account_id=a.id AND b.date=(SELECT MAX(date) FROM balances WHERE account_id=a.id) ORDER BY a.name")]


def networth_series(con, entity: str | None = None) -> list[dict]:
    """Patrimonio por fecha (suma del último saldo conocido de cada cuenta en esa fecha)."""
    ent, args = ("AND a.entity=?", (entity,)) if entity else ("", ())
    rows = con.execute(f"SELECT b.account_id, b.date, b.amount FROM balances b JOIN accounts a ON a.id=b.account_id WHERE 1=1 {ent} ORDER BY b.date", args).fetchall()
    last: dict[int, float] = {}
    out: dict[str, float] = {}
    first: dict[int, str] = {}
    for r in rows:
        first.setdefault(r["account_id"], r["date"])
        last[r["account_id"]] = r["amount"]
        out[r["date"]] = sum(last.values())
    total = sum(abs(v) for v in last.values()) or 1
    material = [a for a, v in last.items() if abs(v) >= 0.01 * total]          # las cuentas de montos mínimos no deciden dónde empieza la línea
    start = max((first[a] for a in material), default="")                      # antes de esa fecha faltan cuentas importantes y el total no es real
    return [{"date": d, "total": round(v)} for d, v in sorted(out.items()) if d >= start]


def goals(con) -> list[dict]:
    out = []
    for g in con.execute("SELECT g.*, (SELECT b.amount FROM balances b WHERE b.account_id=g.account_id ORDER BY b.date DESC LIMIT 1) AS acc_amount FROM savings_goals g WHERE g.status='active' ORDER BY g.deadline IS NULL, g.deadline"):
        saved = g["acc_amount"] if g["account_id"] and g["acc_amount"] is not None else g["saved"]
        pct = min(100, round(100 * saved / g["target"])) if g["target"] else 0
        need = None
        if g["deadline"] and g["target"] > saved:
            left_m = max(1, (date.fromisoformat(g["deadline"]).year - date.today().year) * 12 + date.fromisoformat(g["deadline"]).month - date.today().month)
            need = round((g["target"] - saved) / left_m)
        out.append({"id": g["id"], "name": g["name"], "target": g["target"], "saved": saved, "pct": pct, "deadline": g["deadline"], "per_month": need})
    return out


# ---------- recomendaciones ----------
def insights(con, month: str | None = None, entity: str | None = None, today: date | None = None) -> list[dict]:
    today = today or date.today()
    month = month or today.strftime("%Y-%m")
    s = money.month_summary(con, month, entity)
    prev = money.month_summary(con, _month_back(month, 1), entity)
    out: list[dict] = []
    inc, exp = s["income"], s["expense"]
    try:  # con meses incompletos (falta el extracto de alguna cuenta) no se sacan conclusiones de ahorro ni de comparación
        from . import statements
        cov = statements.coverage(con, 12, today)
        full = lambda m: (not cov["rows"]) or m not in cov["months"] or cov["complete"][cov["months"].index(m)]
        complete, prev_complete = full(month), full(_month_back(month, 1))
    except Exception:
        complete = prev_complete = True
    if inc > 0 and complete:
        rate = round(100 * (inc - exp) / inc)
        if rate >= 20:
            out.append({"level": "ok", "text": f"Ahorraste el {rate}% de tus ingresos: por encima del 20% que recomienda la regla 50/30/20."})
        elif rate >= 10:
            out.append({"level": "info", "text": f"Ahorras el {rate}% de tus ingresos. La meta clásica es 20%: te faltan {fmt(0.2 * inc - (inc - exp))} al mes."})
        else:
            out.append({"level": "warn", "text": "Gastas casi todo lo que ingresas" if rate >= 0 else "Este mes gastaste más de lo que ingresaste" + f" (neto {fmt(inc - exp)}). Revisa las categorías más grandes."})
    cats = s["by_category"]
    if cats and exp:
        top = cats[0]
        out.append({"level": "info", "text": f"Tu mayor gasto es «{top['category']}»: {fmt(-top['total'])} ({round(100 * -top['total'] / (sum(-c['total'] for c in s['by_category']) or 1))}% de lo que gastaste)."})
    pcat = {c["category"]: -c["total"] for c in prev["by_category"]} if (complete and prev_complete) else {}
    for c in cats[:6]:
        before = pcat.get(c["category"])
        if before and -c["total"] > before * 1.25 and -c["total"] - before > 50000:
            out.append({"level": "warn", "text": f"«{c['category']}» subió {round(100 * (-c['total'] - before) / before)}% frente al mes pasado (+{fmt(-c['total'] - before)})."})
    for c in cats:
        if c["over_budget"]:
            out.append({"level": "warn", "text": f"Te pasaste del presupuesto en «{c['category']}»: {fmt(-c['total'])} de {fmt(c['budget'])}."})
    if month == today.strftime("%Y-%m") and exp and today.day >= 5:
        dim = calendar.monthrange(today.year, today.month)[1]
        proj = exp / today.day * dim
        out.append({"level": "info", "text": f"Al ritmo de este mes cerrarás con gastos de ≈{fmt(proj)}" + (f" (el mes pasado: {fmt(prev['expense'])})." if prev["expense"] else ".")})
    if s["uncategorized"]:
        out.append({"level": "info", "text": "Hay movimientos sin categoría: ponles una para que los gráficos y presupuestos sean exactos."})
    bal = [b for b in latest_balances(con) if b["amount"] is not None and b["kind"] in ("savings", "bank", "cash", "invest")]
    series = [m for m in monthly_series(con, 6, entity) if m["expense"]]
    if bal and series:
        runway = sum(b["amount"] for b in bal if b["amount"] > 0) / (sum(m["expense"] for m in series) / len(series))
        out.append({"level": "ok" if runway >= 3 else "warn", "text": f"Tu colchón cubre ≈{runway:.1f} meses de gastos (se recomienda de 3 a 6)."})
    for c in cards(con):
        if c["util"] >= 30 and c["credit_limit"]:
            out.append({"level": "warn", "text": f"{c['name']}: usas el {c['util']}% del cupo. Lo sano es menos del 30% (cuida tu historial crediticio)."})
        if c["pending_installments"]:
            out.append({"level": "info", "text": f"{c['name']}: tienes {fmt(c['pending_installments'])} en cuotas por facturar en los próximos meses."})
    fx = con.execute("SELECT COALESCE(SUM(-amount),0) FROM transactions WHERE description LIKE 'Comisión por cambio%' AND date >= date('now','-90 day')").fetchone()[0]
    if fx > 5000:
        out.append({"level": "info", "text": f"Pagaste {fmt(fx)} en comisiones por cambio de moneda en 90 días (compras en dólares). Una tarjeta sin comisión internacional te lo ahorraría."})
    nequi = next((c for c in s["by_category"] if c["category"] == "Gastos vía Nequi"), None)
    if nequi and exp and -nequi["total"] / exp > 0.2:
        out.append({"level": "info", "text": f"El {round(100 * -nequi['total'] / exp)}% de tus gastos sale por Nequi y no se ve en qué. Si quieres el detalle, exporta el extracto de Nequi y lo clasifico."})
    try:
        from . import statements
        cov = statements.coverage(con, 6, today)
        if month in cov["months"]:
            i = cov["months"].index(month)
            missing = [r["account"] for r in cov["rows"] if not r["cells"][i] and not r["lag"]]
            if s.get("income_assumed"):
                out.insert(0, {"level": "info", "text": f"Ingreso supuesto de {fmt(s['income'])} para {month}: aún no tengo el extracto de Bancolombia de ese mes. Los gastos que salen de Bancolombia tampoco están incluidos. Cuando lo subas, lo reemplazo por lo real."})
            if missing:
                out.insert(0, {"level": "warn", "text": f"Este mes está incompleto: faltan extractos de {', '.join(missing)}. Los totales y consejos pueden no ser exactos."})
    except Exception:
        pass
    try:
        tl = transfer_links(con, today)
        by_acc: dict[str, list] = {}
        for p in tl["pending"]:
            by_acc.setdefault(p["other"], []).append(p)
        for acc_name, items in by_acc.items():
            tot_out = sum(-p["amount"] for p in items if p["amount"] < 0)
            tot_in = sum(p["amount"] for p in items if p["amount"] > 0)
            bits = ([f"enviaste {fmt(tot_out)}"] if tot_out else []) + ([f"recibiste {fmt(tot_in)}"] if tot_in else [])
            out.append({"level": "info", "text": f"{' y '.join(bits).capitalize()} con {acc_name} ({len(items)} {"traslado" if len(items) == 1 else "traslados"}) pero aún no tengo su extracto de esas fechas: no es gasto ni ingreso, y cuando lo subas se cuadra solo."})
        if tl["review"]:
            out.append({"level": "warn", "text": f"{len(tl['review'])} traslados no tienen su par en la otra cuenta aunque ya hay extracto de esas fechas ({fmt(sum(abs(p['amount']) for p in tl['review']))}): revísalos en «Traslados entre tus cuentas» de Finanzas."})
        fe = fund_estimate(con)
        if fe:
            out.append({"level": "info", "text": f"Fiducuenta: su último extracto llega al {fe['since']}; desde entonces enviaste {fmt(fe['moved'])}, así que debería tener ≈{fmt(fe['estimate'])} (más rendimientos)."})
    except Exception:
        pass
    pend = con.execute("SELECT COUNT(*), COALESCE(SUM(ABS(amount)),0) FROM transactions WHERE category='Por aclarar'").fetchone()
    if pend[0]:
        out.append({"level": "info", "text": f"Hay {pend[0]} movimientos grandes «por aclarar» ({fmt(pend[1])} en total) que no se cuentan como ingreso ni gasto hasta que digas qué fueron (ver la lista en Finanzas)."})
    if not out:
        out.append({"level": "info", "text": "Importa un extracto o registra tus saldos para recibir recomendaciones."})
    return out


# ---------- gráficos SVG ----------
def bars_svg(series: list[dict]) -> str:
    W, H, pl, pb = 640, 230, 46, 26
    top = max([m["income"] for m in series] + [m["expense"] for m in series] + [1])
    step = 10 ** (len(str(int(top))) - 1)
    top = ((int(top) // step) + 1) * step
    bw, gap = 14, (W - pl - 10) / len(series)
    g = [f'<line class="c-grid" x1="{pl}" x2="{W - 6}" y1="{H - pb - (H - pb - 10) * k / 4:.1f}" y2="{H - pb - (H - pb - 10) * k / 4:.1f}"/>'
         f'<text class="c-lbl" x="{pl - 6}" y="{H - pb - (H - pb - 10) * k / 4 + 4:.1f}" text-anchor="end">{fmt(top * k / 4 / 1000)}k</text>' for k in range(5)]
    for i, m in enumerate(series):
        x = pl + i * gap + gap / 2
        for j, (key, cls) in enumerate((("income", "c-inc"), ("expense", "c-exp"))):
            h = (H - pb - 10) * m[key] / top
            g.append(f'<rect class="{cls}" x="{x - bw + j * (bw + 1):.1f}" y="{H - pb - h:.1f}" width="{bw}" height="{max(h, 0.5):.1f}" rx="3"><title>{m["label"]} · {"ingresos" if key == "income" else "gastos"} {fmt(m[key])}</title></rect>')
        g.append(f'<text class="c-lbl" x="{x:.1f}" y="{H - 8}" text-anchor="middle">{m["label"]}</text>')
    return f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Ingresos y gastos por mes">{"".join(g)}</svg>'


def donut_svg(items: list[dict], label: str = "gastos") -> tuple[str, list[dict]]:
    """items: [{name, value}] → (svg, leyenda con color y %)."""
    items = [i for i in items if i["value"] > 0]
    total = sum(i["value"] for i in items) or 1
    if len(items) > 7:
        rest = sum(i["value"] for i in items[6:])
        items = items[:6] + [{"name": "otros", "value": rest}]
    R, C = 78, 2 * 3.14159265 * 78
    off, parts, legend = 0.0, [], []
    for n, i in enumerate(items):
        frac = i["value"] / total
        cls = PALETTE[n % len(PALETTE)]
        parts.append(f'<circle class="{cls}" r="{R}" cx="110" cy="110" fill="none" stroke-width="30" stroke-dasharray="{frac * C:.2f} {C:.2f}" stroke-dashoffset="{-off:.2f}" transform="rotate(-90 110 110)"><title>{i["name"]} · {fmt(i["value"])} ({round(100 * frac)}%)</title></circle>')
        legend.append({"name": i["name"], "value": i["value"], "pct": round(100 * frac), "cls": cls})
        off += frac * C
    svg = f'<svg class="chart donut" viewBox="0 0 220 220" role="img" aria-label="Gastos por categoría">{"".join(parts)}<text class="c-big" x="110" y="106" text-anchor="middle">{fmt(total)}</text><text class="c-lbl" x="110" y="126" text-anchor="middle">{label}</text></svg>'
    return svg, legend


def line_svg(points: list[dict]) -> str:
    if len(points) < 2:
        return ""
    W, H, pl, pb = 640, 200, 52, 24
    vals = [p["total"] for p in points]
    lo, hi = min(vals + [0]), max(vals + [1])
    span = (hi - lo) or 1
    n = len(points)
    xy = [(pl + (W - pl - 10) * i / (n - 1), 10 + (H - pb - 16) * (1 - (p["total"] - lo) / span)) for i, p in enumerate(points)]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(xy))
    area = f"{path} L{xy[-1][0]:.1f},{H - pb} L{xy[0][0]:.1f},{H - pb} Z"
    dots = "".join(f'<circle class="c-dot" cx="{x:.1f}" cy="{y:.1f}" r="3.5"><title>{p["date"]} · {fmt(p["total"])}</title></circle>' for (x, y), p in zip(xy, points))
    lbl = (f'<text class="c-lbl" x="{pl}" y="{H - 6}">{points[0]["date"]}</text><text class="c-lbl" x="{W - 10}" y="{H - 6}" text-anchor="end">{points[-1]["date"]}</text>'
           f'<text class="c-lbl" x="{pl - 6}" y="16" text-anchor="end">{fmt(hi / 1000)}k</text><text class="c-lbl" x="{pl - 6}" y="{H - pb}" text-anchor="end">{fmt(lo / 1000)}k</text>')
    return f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Patrimonio en el tiempo"><path class="c-area" d="{area}"/><path class="c-line" d="{path}" fill="none"/>{dots}{lbl}</svg>'


def heat_grid(spend: dict[str, float], month: str) -> dict:
    y, m = map(int, month.split("-"))
    first = date(y, m, 1)
    days = calendar.monthrange(y, m)[1]
    mx = max(spend.values()) if spend else 0
    cells = [None] * first.weekday()
    for d in range(1, days + 1):
        iso = f"{y:04d}-{m:02d}-{d:02d}"
        v = spend.get(iso, 0)
        level = 0 if not v else 1 + min(3, int(4 * v / (mx + 1)))
        cells.append({"day": d, "value": round(v), "level": level})
    return {"cells": cells, "max": round(mx)}


# ---------- importación con detección automática ----------
DATE_HINT = ("fecha", "date", "fec")
DESC_HINT = ("descripcion", "descripción", "detalle", "concepto", "description", "movimiento", "referencia", "comercio")
AMT_HINT = ("valor", "monto", "importe", "amount", "total")
DEB_HINT = ("debito", "débito", "retiro", "cargo", "egreso", "debit")
CRE_HINT = ("credito", "crédito", "abono", "deposito", "depósito", "ingreso", "credit")


def detect(file: Path) -> dict:
    """Mira el archivo y adivina separador, codificación, fila de títulos y columnas (fecha, descripción, monto o débito/crédito)."""
    raw = file.read_bytes()
    for enc in ("utf-8-sig", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    lines = [l for l in text.splitlines() if l.strip()]
    delim = max((";", ",", "\t", "|"), key=lambda d: sum(l.count(d) for l in lines[:20]))
    header_i = next((i for i, l in enumerate(lines[:30]) if any(h in l.lower() for h in DATE_HINT) and l.count(delim) >= 2), 0)
    head = [c.strip().strip('"') for c in next(csv.reader([lines[header_i]], delimiter=delim))]
    low = [h.lower() for h in head]
    pick = lambda hints: next((head[i] for i, h in enumerate(low) if any(x in h for x in hints)), None)
    cols = {"date": pick(DATE_HINT), "desc": pick(DESC_HINT), "amount": pick(AMT_HINT), "debit": pick(DEB_HINT), "credit": pick(CRE_HINT)}
    if not cols["date"] or not cols["desc"] or not (cols["amount"] or (cols["debit"] and cols["credit"])):
        raise ValueError("No pude identificar las columnas (fecha, descripción y monto o débito/crédito). Ábrelo en «Columnas del archivo» y ajústalas.")
    return {"encoding": enc, "delimiter": delim, "header_row": header_i, "columns": cols, "text": "\n".join(lines[header_i:])}


def import_auto(con, file: Path, account: str) -> tuple[int, int, dict]:
    d = detect(file)
    tmp = file.with_suffix(".auto.csv")
    tmp.write_text(d["text"], encoding="utf-8")
    try:
        c = d["columns"]
        added, skipped = money.import_file(con, tmp, account, c["date"], c["desc"], c["amount"] if c["amount"] and not (c["debit"] and c["credit"]) else None,
                                           c["debit"], c["credit"], d["delimiter"], None, "utf-8")
    finally:
        tmp.unlink(missing_ok=True)
    return added, skipped, {k: v for k, v in d.items() if k != "text"}


# ---------- CLI ----------
@app.command("balance")
def balance_cmd(account: str, amount: float, day: str = typer.Option(None, help="YYYY-MM-DD (por defecto hoy)")) -> None:
    """Registra el saldo de una cuenta (ahorros, tarjeta con saldo negativo, efectivo…) para ver el patrimonio en el tiempo."""
    try:
        with db.session() as con:
            set_balance(con, account, amount, day)
    except ValueError as e:
        raise typer.BadParameter(str(e))
    typer.echo("ok")


@app.command("pocket")
def pocket_cmd(amount: float, day: str = typer.Option(None, help="YYYY-MM-DD de ese saldo"), estimated: bool = typer.Option(False, help="Marca el valor como estimado")) -> None:
    """Saldo de tu bolsillo en la RappiCuenta de tu familiar (el extracto no lo separa). El sistema lo mueve mes a mes con el rendimiento."""
    with db.session() as con:
        set_pocket(con, amount, day, estimated)
    typer.echo("ok")


@app.command("goal")
def goal_cmd(name: str, target: float, deadline: str = typer.Option(None, help="YYYY-MM-DD"), account: str = typer.Option(None, help="Cuenta cuyo saldo es lo ahorrado"),
             saved: float = 0) -> None:
    """Crea una meta de ahorro."""
    with db.session() as con:
        acc = con.execute("SELECT id FROM accounts WHERE name=?", (account,)).fetchone() if account else None
        con.execute("INSERT INTO savings_goals(name, target, deadline, account_id, saved) VALUES (?,?,?,?,?)", (name, target, deadline, acc["id"] if acc else None, saved))
        db.audit(con, "money.goal", "WRITE", name, detail=f"{target:,.0f}")
    typer.echo("ok")


@app.command("insights")
def insights_cmd(as_json: bool = JSON) -> None:
    """Recomendaciones sobre tus finanzas de este mes."""
    with db.session() as con:
        rows = insights(con)
    typer.echo(json.dumps(rows, ensure_ascii=False, indent=2) if as_json else "\n".join(f"[{r['level']}] {r['text']}" for r in rows))


# ---------- extractos: tarjetas, composición, cobertura y cobros recurrentes ----------
def cards(con) -> list[dict]:
    """Último corte de cada tarjeta: cuánto pagar, hasta cuándo, uso del cupo y cuotas pendientes."""
    out = []
    for c in con.execute("SELECT a.name, c.* FROM card_statements c JOIN accounts a ON a.id=c.account_id "
                         "WHERE c.period_end=(SELECT MAX(period_end) FROM card_statements WHERE account_id=c.account_id) ORDER BY c.due_date"):
        util = round(100 * (c["used"] or 0) / c["credit_limit"]) if c["credit_limit"] else 0
        out.append({**dict(c), "util": util, "days_left": (date.fromisoformat(c["due_date"]) - date.today()).days if c["due_date"] else None})
    return out


def composition(con) -> list[dict]:
    """Dónde está tu patrimonio (solo activos), para la dona."""
    return [{"name": b["name"], "value": b["amount"]} for b in sorted(latest_balances(con), key=lambda b: -(b["amount"] or 0)) if (b["amount"] or 0) > 0]


def recurring(con, months: int = 4) -> list[dict]:
    """Cobros que se repiten (suscripciones y similares): mismo comercio en 2 o más meses distintos."""
    rows = con.execute(f"SELECT t.description d, t.amount a, substr(t.date,1,7) m FROM transactions t WHERE t.amount<0 AND t.date >= date('now', '-{months} month') "
                       f"AND {money.NOT_TRANSFER} AND COALESCE(t.category,'') IN ('Suscripciones y software','Servicios y telefonía','Deporte','Educación')").fetchall()
    agg: dict[str, dict] = {}
    for r in rows:
        key = " ".join(re.sub(r"[\d*#/\-_.]+", " ", r["d"]).split()[:2]).title()
        e = agg.setdefault(key, {"name": key, "months": set(), "total": 0.0, "count": 0})
        e["months"].add(r["m"])
        e["total"] += -r["a"]
        e["count"] += 1
    out = [{"name": e["name"], "months": len(e["months"]), "count": e["count"], "per_month": round(e["total"] / len(e["months"]))} for e in agg.values() if len(e["months"]) >= 2]
    return sorted(out, key=lambda x: -x["per_month"])[:10]


def to_clarify(con) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT t.id, t.date, t.description, t.amount, a.name AS account FROM transactions t JOIN accounts a ON a.id=t.account_id "
                                         "WHERE t.category IN ('Por aclarar','Pagos PSE (por aclarar)') ORDER BY t.date DESC")]


def monthly_report(con, month: str) -> str:
    """Resumen corto del mes para Telegram (lo envía el job de extractos el día 6 de cada mes)."""
    from . import statements
    s = money.month_summary(con, month)
    cov = statements.coverage(con, 6)
    i = cov["months"].index(month) if month in cov["months"] else None
    missing = [r["account"] for r in cov["rows"] if i is not None and not r["cells"][i] and not r["lag"]]
    L = [f"📊 Tus finanzas de {month}", f"Ingresos (trabajo{', supuesto: falta el extracto de Bancolombia' if s.get('income_assumed') else ''}) {fmt(s['income'])} · Gastos {fmt(s['expense'])} · Neto {fmt(s['net'])}", ""]
    L += [f"• {c['category']}: {fmt(-c['total'])}" for c in s["by_category"][:6]]
    nw = networth_series(con)
    if nw:
        L += ["", f"🏦 Patrimonio: {fmt(nw[-1]['total'])}"]
    for c in cards(con):
        if c["days_left"] is not None and c["days_left"] >= 0:
            L.append(f"💳 {c['name']}: paga {fmt(c['total_due'])} antes del {c['due_date']}")
    tips = [t for t in insights(con, month) if t["level"] == "warn"][:3]
    if tips:
        L += [""] + [f"⚠️ {t['text']}" for t in tips]
    manual = [m for m in missing if m.startswith(("Bancolombia", "Fiducuenta"))]
    auto = [m for m in missing if m not in manual]
    if manual:
        L += ["", "📥 Para completar el mes, descarga y deja en Documentos\\PERSONAL_OS-Finanzas\\Extractos: " + ", ".join(manual) + "."]
    if auto:
        L += [f"📨 {', '.join(auto)}: llega solo al correo; si no te llegó, reenvíalo a tu Hotmail."]
    return "\n".join(L)


def default_month(con, today: date | None = None) -> str:
    """Mes a mostrar por defecto: el más reciente con extractos de TODAS las cuentas; si no hay, el último con movimientos."""
    from . import statements
    cov = statements.coverage(con, 12, today)
    done = [m for m, ok in zip(cov["months"], cov["complete"]) if ok]
    if done:
        return done[-1]
    last = con.execute("SELECT MAX(substr(date,1,7)) FROM transactions").fetchone()[0]
    return last or (today or date.today()).strftime("%Y-%m")


def month_complete(con, month: str, today: date | None = None) -> bool:
    from . import statements
    cov = statements.coverage(con, 12, today)
    return (not cov["rows"]) or month not in cov["months"] or cov["complete"][cov["months"].index(month)]
