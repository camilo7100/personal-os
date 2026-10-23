"""Extractos: lectores por banco (con datos ficticios), categorías, movimientos internos y cobertura."""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("POS_DB", str(tmp_path / "pos.db"))
    monkeypatch.setenv("POS_HOME", str(tmp_path))
    for m in [m for m in sys.modules if m == "pos" or m.startswith("pos.")]:
        del sys.modules[m]
    from pos import db, finance, money, statements
    db.init_db()
    monkeypatch.setattr(statements, "ARCHIVE", tmp_path / "archivo")
    return db, statements, money, finance


def bancolombia_xlsx(path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in (["Información Cliente:"], ["CLIENTE", "DIRECCIÓN", "CIUDAD"], ["PERSONA DE PRUEBA", "calle 1", "CIUDAD"], ["Información General:"],
              ["DESDE", "HASTA", "TIPO CUENTA", "NRO CUENTA", "SUCURSAL"], ["2026/03/31", "2026/06/30", "CUENTA DE AHORROS", "000111", "SUC"],
              ["Resumen:"], ["SALDO ANTERIOR", "TOTAL ABONOS", "TOTAL CARGOS", "SALDO ACTUAL"], ["1,000,000.00", "5,000,000.00", "4,200,000.00", "1,800,000.00"],
              ["Movimientos:"], ["FECHA", "DESCRIPCIÓN", "SUCURSAL", "DCTO.", "VALOR", "SALDO"],
              ["15/04", "PAGO DE NOMI EMPRESA X", None, None, "5,000,000.00", "6,000,000.00"],
              ["16/04", "TRASLADO A FONDO DE INVERSION", None, None, "-3,000,000.00", "3,000,000.00"],
              ["17/04", "TRANSFERENCIAS A NEQUI", None, None, "-200,000.00", "2,800,000.00"],
              ["20/04", "PAGO PSE NU Compania de Fina", None, None, "-500,000.00", "2,300,000.00"],
              ["21/04", "PAGO QR AREPAS LA ESQUINA", None, None, "-50,000.00", "2,250,000.00"],
              ["22/04", "ABONO INTERESES AHORROS", None, None, "1.50", "2,250,001.50"],
              [None, "FIN ESTADO DE CUENTA"]):
        ws.append(r)
    wb.save(path)


RAPPICUENTA = """Periodo
1 SEP - 30 SEP (30 días)
Extracto de cuenta generado el 01 de Octubre de 2026
Extracto de tu RappiCuenta
Saldo anterior
$1,000,000.00
Saldo final
$1,050,000.00
Fecha
Descripción
Valor
09 Sep 2026
Redención Cashback
+$5,000.00
11 Sep 2026
Envío a otra llave
-$20,000.00
30 Sep 2026
Intereses ganados
+$65,000.00
"""

NU = """Hola
Resumen de tu extracto
Tu cupo definido $2.000.000,00
Usado
$900.000,00
$300.000,00
DEUDA TOTAL HASTA EL 26 SEPTIEMBRE
PAGO MÍNIMO
$300.000,00
Fecha límite de pago
19 OCT 2026
Fecha de corte
27 SEP 2026
Fecha
Descripción
Valor
Restante
22 SEP
2026
Anthropic
$15.000,00
1 de 1
↪ Comisión por cambio de moneda
$70,00
14 SEP
2026
Pago
$250.000,00
$0,00
10 SEP 2026
Tienda D1 Centro
$35.000,00
1 de 1
Pago mínimo
$300.000,00
"""

RAPPICARD = """Extracto de tarjeta de crédito
Producto emitido por Davivienda S.A.
Cupo total: $5.000.000,00
Pago mínimo
$ 400.000,00
Pago total
$ 1.200.000,00
Periodo facturado
Desde
31 jul 2026
Hasta
28 ago 2026
Fecha de pago
10 sept 2026
Virtual
2026-08-02
CINE COLOMBIA
$50.000,00
$50.000,00
1 de 1
$0,00
-
2026-08-05
PAGOS POR PSE
$-1.000.000,00
N/A
N/A
N/A
Virtual
2026-08-14
MERCADO
PAGO*MERCADOLI
$600.000,00
$100.000,00
1 de 6
$500.000,00
"""


def test_parsers_read_each_bank(env, tmp_path):
    _, st, _, _ = env
    f = tmp_path / "b.xlsx"
    bancolombia_xlsx(f)
    b = st.read(f)
    assert b["account"] == "Bancolombia ahorros" and b["closing"] == 1800000 and len(b["transactions"]) == 6 and b["transactions"][0] == ("2026-04-15", "PAGO DE NOMI EMPRESA X", 5000000.0)
    r = st.parse_rappicuenta_pdf(RAPPICUENTA)
    assert (r["start"], r["end"], r["closing"]) == ("2026-09-01", "2026-09-30", 1050000.0) and r["transactions"][1] == ("2026-09-11", "Envío a otra llave", -20000.0)
    n = st.parse_nu_card_pdf(NU)
    assert n["card"]["due"] == "2026-10-19" and n["closing"] == -300000 and n["card"]["limit"] == 2000000
    descs = [t[1] for t in n["transactions"]]
    assert "Anthropic" in descs and "Pago tarjeta Nu" in descs and any(d.startswith("Comisión por cambio") for d in descs) and "Tienda D1 Centro" in descs
    c = st.parse_rappicard_pdf(RAPPICARD)
    assert c["card"] == {"due": "2026-09-10", "minimum": 400000.0, "total": 1200000.0, "limit": 5000000.0, "used": 1200000.0, "pending_installments": 500000.0}
    assert ("2026-08-05", "PAGOS POR PSE", 1000000.0) in c["transactions"] and ("2026-08-14", "MERCADO PAGO*MERCADOLI", -600000.0) in c["transactions"]


def test_import_excludes_internal_moves_and_is_idempotent(env, tmp_path):
    db, st, money, finance = env
    f = tmp_path / "b.xlsx"
    bancolombia_xlsx(f)
    with db.session() as con:
        r = st.import_file(con, f)
        assert r["added"] == 6 and st.import_file(con, f) == {"skipped": "b.xlsx"}
        cats = {t["description"]: t["category"] for t in con.execute("SELECT description, category FROM transactions")}
        assert cats["TRASLADO A FONDO DE INVERSION"] == "Movimiento entre mis cuentas" and cats["PAGO PSE NU Compania de Fina"] == "Pago de tarjeta de crédito"
        assert cats["TRANSFERENCIAS A NEQUI"] == "Gastos vía Nequi" and cats["PAGO QR AREPAS LA ESQUINA"] == "Comida fuera" and cats["PAGO DE NOMI EMPRESA X"] == "Salario"
        s = money.month_summary(con, "2026-04")
        assert s["income"] == 5000000.0 and s["expense"] == 250000      # el traslado al fondo y el pago de la tarjeta no son gasto
        assert finance.latest_balances(con)[0]["amount"] == 1800000
        cov = st.coverage(con, 4, date(2026, 6, 30))
        assert cov["months"] == ["2026-03", "2026-04", "2026-05", "2026-06"] and cov["rows"][0]["cells"] == [False, True, True, True]


def test_card_statement_updates_payment_task(env, monkeypatch):
    db, st, _, finance = env
    from pos import services

    class Oct1(date):  # congela «hoy» para que la prueba no dependa del día en que se corra
        @classmethod
        def today(cls):
            return cls(2026, 10, 1)
    monkeypatch.setattr(st, "date", Oct1)
    with db.session() as con:
        services.add_task(con, "Pagar tarjeta Nu (límite día 19: pagar hoy)", due="2026-10-18", tags="pago,nu", repeat="monthly")
        n = st.parse_nu_card_pdf(NU)
        st.import_parsed(con, n, "test")
        t = con.execute("SELECT notes, due_date FROM tasks").fetchone()
        assert "300.000" in t["notes"] and t["due_date"] == "2026-10-18"
        c = finance.cards(con)[0]
        assert c["util"] == 15 and c["due_date"] == "2026-10-19"


def test_networth_starts_when_all_accounts_known_and_default_month(env, tmp_path):
    db, st, money, finance = env
    f = tmp_path / "b.xlsx"
    bancolombia_xlsx(f)
    with db.session() as con:
        st.import_file(con, f)                                   # Bancolombia cubre abril–junio de 2026
        finance.set_balance(con, "Bancolombia ahorros", 2000000, "2026-05-31")
        con.execute("INSERT INTO accounts(name, kind) VALUES ('Fondo', 'invest')")
        finance.set_balance(con, "Fondo", 100000000, "2026-06-15")
        series = finance.networth_series(con)
        assert series[0]["date"] == "2026-06-15"                 # antes de esa fecha faltaba el fondo: el total no era real
        assert finance.default_month(con, date(2026, 7, 1)) == "2026-06"
        assert finance.month_complete(con, "2026-05", date(2026, 7, 1))
        st.import_parsed(con, st.parse_rappicuenta_pdf(RAPPICUENTA), "t")  # una cuenta nueva que solo cubre septiembre
        assert not finance.month_complete(con, "2026-06", date(2026, 10, 1))
        txt = finance.insights(con, "2026-06", today=date(2026, 10, 1))
        assert any("incompleto" in t["text"] for t in txt)


def test_income_is_only_work_and_transfers_wait_for_the_other_statement(env):
    db, st, money, finance = env
    with db.session() as con:
        for name, kind in (("Bancolombia ahorros", "savings"), ("Fiducuenta (inversión)", "invest")):
            con.execute("INSERT INTO accounts(name, kind) VALUES (?,?)", (name, kind))
        a = con.execute("SELECT id FROM accounts WHERE name LIKE 'Banco%'").fetchone()[0]
        for d, desc, amt in (("2026-05-15", "PAGO DE NOMI EMPRESA X", 7200000), ("2026-05-16", "TRASLADO A FONDO DE INVERSION", -3000000),
                             ("2026-05-17", "TRANSF DE MARIA", 100000), ("2026-05-20", "ABONO INTERESES AHORROS", 5000), ("2026-05-21", "PAGO QR AREPAS", -50000)):
            con.execute("INSERT INTO transactions(account_id, date, description, amount, category, hash) VALUES (?,?,?,?,?,?)", (a, d, desc, amt, st.categorize(desc, amt), desc + d))
        s = money.month_summary(con, "2026-05")
        assert s["income"] == 7200000 and s["expense"] == 50000                      # ni el traslado ni lo de familia/intereses son ingreso o gasto
        assert {o["category"] for o in s["other_in"]} == {"Familia (recibido)", "Intereses y rendimientos"}
        tl = finance.transfer_links(con)
        assert tl["pairs"] == 0 and [p["other"] for p in tl["pending"]] == ["Fiducuenta (inversión)"]   # espera el extracto de la Fiducuenta
        f = con.execute("SELECT id FROM accounts WHERE name LIKE 'Fiducuenta%'").fetchone()[0]
        con.execute("INSERT INTO transactions(account_id, date, description, amount, category, hash) VALUES (?,?,?,?,?,?)", (f, "2026-05-17", "Adicion", 3000000, "Movimiento entre mis cuentas", "adi"))
        assert finance.transfer_links(con)["pairs"] == 1 and not finance.transfer_links(con)["pending"]  # al llegar el otro lado se cuadra solo


def test_day_15_creates_upload_task_counts_each_account_and_holds_telegram_files(env, tmp_path, monkeypatch):
    db, st, money, finance = env
    monkeypatch.setattr(st, "HOLD_DIR", tmp_path / "recibidos")
    with db.session() as con:
        for name in ("Bancolombia ahorros", "Nu (crédito)", "Nequi (deuda, estimada)"):
            con.execute("INSERT INTO accounts(name) VALUES (?)", (name,))
        assert st.ensure_upload_task(con, date(2026, 10, 14)) is None            # antes del 15 no hace nada
        tid = st.ensure_upload_task(con, date(2026, 10, 15))
        assert tid and st.ensure_upload_task(con, date(2026, 10, 20)) is None    # una sola vez por mes
        titles = [r["title"] for r in con.execute("SELECT title FROM tasks WHERE parent_id=?", (tid,))]
        assert titles == ["Extracto de Bancolombia ahorros", "Extracto de Nu (crédito)"]   # Nequi no tiene extracto
        p = st.tick_checklist(con, date(2026, 10, 15))
        assert p["got"] == [] and len(p["missing"]) == 2
        nu = con.execute("SELECT id FROM accounts WHERE name LIKE 'Nu%'").fetchone()[0]
        con.execute("INSERT INTO statement_periods(account_id, start, end) VALUES (?,?,?)", (nu, "2026-09-01", "2026-09-30"))
        p = st.tick_checklist(con, date(2026, 10, 15))
        assert p["got"] == ["Nu (crédito)"] and p["missing"] == ["Bancolombia ahorros"]
        assert con.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0] != "done"
        assert "1/2" in st.progress_message(p) and "Bancolombia" in st.progress_message(p)
        ba = con.execute("SELECT id FROM accounts WHERE name LIKE 'Banco%'").fetchone()[0]
        con.execute("INSERT INTO statement_periods(account_id, start, end) VALUES (?,?,?)", (ba, "2026-07-01", "2026-09-30"))
        p = st.tick_checklist(con, date(2026, 10, 15))
        assert not p["missing"] and con.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0] == "done"  # con todos, la tarea se cierra sola
    f = tmp_path / "x.tmp"
    f.write_text("fake")
    kept = st.hold_file(f, "Extracto Bancolombia (agosto).pdf")
    assert kept.exists() and not f.exists() and st.held() == [kept]             # se guarda sin importar


def test_missing_bancolombia_month_assumes_7m_income_and_replaces_it_when_the_statement_arrives(env, tmp_path):
    db, st, money, finance = env
    f = tmp_path / "b.xlsx"
    bancolombia_xlsx(f)
    with db.session() as con:
        st.import_file(con, f)                                    # Bancolombia cubre hasta junio
        st.import_parsed(con, st.parse_rappicuenta_pdf(RAPPICUENTA), "t")   # otra cuenta con septiembre
        assert money.month_summary(con, "2026-04")["income_assumed"] is False
        s = money.month_summary(con, "2026-09")
        assert s["income"] == 7000000 and s["income_assumed"] is True             # supuesto mientras falte
        assert any("supuesto" in t["text"].lower() for t in finance.insights(con, "2026-09", today=date(2026, 10, 1)))
        ba = con.execute("SELECT id FROM accounts WHERE name LIKE 'Banco%'").fetchone()[0]
        con.execute("INSERT INTO statement_periods(account_id, start, end) VALUES (?,?,?)", (ba, "2026-07-01", "2026-09-30"))
        s = money.month_summary(con, "2026-09")
        assert s["income_assumed"] is False and s["income"] == 0                   # llegó el extracto: manda lo real


def test_fiducuenta_pdf_gives_the_same_movements_as_the_xlsx(env):
    db, st, money, finance = env
    text = ("FONDO DE INVERSIÓN COLECTIVA ABIERTO\nFIDUCUENTA FIDUCIARIA BANCOLOMBIA\nEXTRACTO MENSUAL\nCuenta de Inversión:\n999\nDesde: 20260801\nHasta:\n20260831\n"
            "SALDO ANTERIOR\nVALOR EN PESOS\n100.000.000,00\n2.000,10\n3.000.000,00\n1.000.000,00\nREND. NETOS\n500.000,00\n20.000,00\n102.480.000,00\n2.050,00\n"
            "FECHA.\nTRANSACCIÓN\n20260804\nRETIRO TRASLA\n1.000.000,00\n20,5\n99.000.000,00\n20260819\nADICION\n3.000.000,00\n60,1\n102.000.000,00\n20260810\nRETEFTE\n20.000,00\n0,4\n98.000.000,00\n")
    s = st.parse_fiducuenta_pdf(text)
    assert (s["start"], s["end"], s["opening"], s["closing"]) == ("2026-08-01", "2026-08-31", 100000000.0, 102480000.0)
    assert sorted(s["transactions"]) == sorted([("2026-08-04", "Retiro Trasla", -1000000.0), ("2026-08-19", "Adicion", 3000000.0),
                                                ("2026-08-10", "Retefte Fiducuenta", -20000.0), ("2026-08-31", "Rendimientos netos Fiducuenta", 500000.0)])
    with db.session() as con:
        assert st.import_parsed(con, s, "t")["added"] == 4
        assert st.import_parsed(con, s, "t")["added"] == 0                                    # reimportar no duplica


def test_papa_rappicuenta_only_counts_my_pocket_and_lagging_accounts_are_estimated(env):
    db, st, money, finance = env
    def papa(ym_end, opening, closing, interest, ret):
        s = (f"Periodo\n1 AGO - 31 AGO\nExtracto de cuenta generado el 01 de Septiembre de 2026\nExtracto de tu RappiCuenta\nTitular De Prueba\nNúmero de cuenta:\n100000001\n"
             f"Saldo al final del periodo:\n${closing}\nResumen del periodo\nSaldo anterior\n${opening}\nAbonos\n+$1.00\nIntereses ganados\n+${interest}\nRetiros\n-$1.00\n"
             f"Comisiones\n$0.00\nGMF 4x1000\n$0.00\nRetención en la fuente\n-${ret}\nIva\n$0.00\nSaldo final\n${closing}\n")
        return s
    text = papa("08", "100,000,000.00", "100,900,000.00", "1,000,000.00", "100,000.00")
    s = st.parse_rappicuenta_pdf(text)
    assert s["holder"] == "Titular De Prueba" and s["number"] == "100000001"
    fam = st.parse_family_rappicuenta(text, s)
    assert fam["third_party"] and fam["account"] == st.POCKET and fam["transactions"] == [] and fam["interest_net"] == 900000.0
    with db.session() as con:
        r = st.import_parsed(con, fam, "t")
        assert r["family"] and con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0        # sus movimientos no son míos
        assert con.execute("SELECT COUNT(*) FROM balances WHERE account_id=(SELECT id FROM accounts WHERE name=?)", (st.POCKET,)).fetchone()[0] == 0  # y su saldo total tampoco es mío
        assert abs(finance.pocket_rate(con) - 0.009) < 1e-9
        finance.set_pocket(con, 50000000.0, "2026-09-30", estimated=True)                                  # tu bolsillo: lo das tú (o un estimado)
        bal = {r["date"]: (r["amount"], r["estimated"]) for r in con.execute("SELECT date, amount, estimated FROM balances WHERE account_id=(SELECT id FROM accounts WHERE name=?)", (st.POCKET,))}
        assert bal["2026-09-30"] == (50000000.0, 1) and bal["2026-07-31"][1] == 1 and bal["2026-08-31"][0] < 50000000.0   # hacia atrás con el rendimiento
        finance.refresh_estimates(con, date(2026, 10, 15))
        assert con.execute("SELECT amount FROM balances WHERE account_id=(SELECT id FROM accounts WHERE name=?) AND date='2026-09-30'", (st.POCKET,)).fetchone()[0] == 50000000.0   # la base no se pierde
        assert money.is_lag_account(st.POCKET) and money.is_lag_account("Fiducuenta (inversión)") and not money.is_lag_account("Nu (crédito)")
        fid = st.parse_fiducuenta_pdf(("FIDUCUENTA FIDUCIARIA BANCOLOMBIA\nEXTRACTO MENSUAL\nDesde: 20260801\nHasta:\n20260831\nSALDO ANTERIOR\n100.000.000,00\n2.000,10\n0,00\n0,00\n"
                                      "900.000,00\n0,00\n100.900.000,00\n2.050,00\nFECHA.\n"))
        st.import_parsed(con, fid, "t")
        finance.refresh_estimates(con, date(2026, 10, 15))
        est = con.execute("SELECT date, amount, estimated FROM balances WHERE account_id=(SELECT id FROM accounts WHERE name LIKE 'Fiducuenta%') AND estimated=1").fetchall()
        assert [e["date"] for e in est] == ["2026-09-30"] and 101000000 < est[0]["amount"] < 102000000       # septiembre supuesto con el rendimiento de agosto
        real = st.parse_fiducuenta_pdf(("FIDUCUENTA FIDUCIARIA BANCOLOMBIA\nEXTRACTO MENSUAL\nDesde: 20260901\nHasta:\n20260930\nSALDO ANTERIOR\n100.900.000,00\n2.000,10\n0,00\n0,00\n"
                                        "800.000,00\n0,00\n101.700.000,00\n2.050,00\nFECHA.\n"))
        st.import_parsed(con, real, "t")
        finance.refresh_estimates(con, date(2026, 10, 15))
        assert not con.execute("SELECT 1 FROM balances WHERE account_id=(SELECT id FROM accounts WHERE name LIKE 'Fiducuenta%') AND estimated=1").fetchone()  # llegó el real: el supuesto desaparece

