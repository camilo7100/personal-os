"""Base de DEMOSTRACIÓN con datos 100 % ficticios («Alex Rivera», analista de datos).

Sirve para enseñar el sistema (capturas, pruebas, README) sin tocar los datos reales:

    python scripts/demo_seed.py            # crea/rehace data\\demo\\pos.db
    python scripts/demo_portal.py          # abre el portal con esa base en http://127.0.0.1:8790

Todo lo que se siembra es relativo a «hoy», así que la demo siempre se ve actual.
"""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "demo"
os.environ["POS_HOME"] = str(DEMO)  # antes de importar pos: así la base y los archivos quedan aparte
os.environ["POS_DB"] = str(DEMO / "pos.db")
sys.path.insert(0, str(ROOT / "scripts"))

from pos import db, finance, life, plans, services  # noqa: E402

TODAY = date.today()
D = lambda n: (TODAY + timedelta(days=n)).isoformat()  # noqa: E731


def seed() -> None:
    if DEMO.exists():
        for f in DEMO.rglob("*"):
            if f.is_file():
                f.unlink()
    DEMO.mkdir(parents=True, exist_ok=True)
    db.DB_PATH = Path(os.environ["POS_DB"])
    with db.session() as con:
        # --- proyectos y metas
        for name, area, due, milestone in (
            ("Maestría: curso final", "estudio", D(60), "Entregar el anteproyecto"),
            ("Búsqueda de empleo", "carrera", None, "Postular a 3 ofertas esta semana"),
            ("Aprender inglés", "estudio", D(240), "Prueba de nivel"),
            ("Mudanza", "casa", D(45), "Cotizar el camión"),
        ):
            services.add_project(con, name, area=area, due=due, milestone=milestone)
        services.add_goal(con, "Conseguir un cargo de analítica senior", horizon="year", due=D(180))
        services.add_goal(con, "Hablar inglés con fluidez (B2)", horizon="year", due=D(240))
        # --- tareas
        for title, project, due, prio, tags in (
            ("Entregar el taller 3 de optimización", "Maestría: curso final", D(0), 1, "maestria"),
            ("Postular a «Senior Data Analyst» (Acme)", "Búsqueda de empleo", D(0), 1, "empleo"),
            ("Pagar la tarjeta de crédito", None, D(2), 2, "pago,finanzas"),
            ("Resumir la semana 4 de simulación", "Maestría: curso final", D(1), 2, "maestria"),
            ("Actualizar la hoja de vida con el último proyecto", "Búsqueda de empleo", D(3), 2, "empleo"),
            ("Prueba de nivel de inglés", "Aprender inglés", D(4), 2, "ingles"),
            ("Cotizar el camión de mudanza", "Mudanza", D(5), 3, "casa"),
            ("Renovar el SOAT", None, D(9), 2, "documentos,vencimiento"),
            ("Llamar al contador por la declaración", None, D(-1), 2, "finanzas"),
            ("Enviar el informe mensual a gerencia", None, D(-1), 1, "trabajo"),
            ("Comprar el regalo de aniversario", None, D(14), 3, "regalo"),
            ("Backup mensual al disco externo", None, D(10), 3, "sistema"),
        ):
            services.add_task(con, title, project=project, due=due, prio=prio, tags=tags)
        # --- hábitos con racha
        life.add_habit(con, "Practicar inglés 30 min", "daily", "Aprender inglés")
        life.add_habit(con, "Caminar 30 min", "daily")
        for h in (1, 2):
            for back in range(0, 6 if h == 1 else 3):
                con.execute("INSERT OR IGNORE INTO habit_log(habit_id, day) VALUES (?,?)", (h, D(-back)))
        # --- personas y fechas
        life.add_person(con, "Sofía (pareja)", "pareja", (TODAY + timedelta(days=21)).strftime("%m-%d"), circle="pareja")
        life.add_person(con, "Mamá", "mamá", (TODAY + timedelta(days=40)).strftime("%m-%d"), circle="familia")
        life.add_person(con, "Papá", "familiar", (TODAY + timedelta(days=120)).strftime("%m-%d"), circle="familia")
        con.execute("INSERT INTO documents(name, kind, expires_on, notes) VALUES (?,?,?,?)", ("SOAT del vehículo", "seguro", D(26), "ficticio"))
        con.execute("INSERT INTO documents(name, kind, expires_on, notes) VALUES (?,?,?,?)", ("Pasaporte", "identidad", D(300), "ficticio"))
        # --- finanzas (11 meses)
        for name, kind in (("Cuenta de ahorros", "savings"), ("Tarjeta de crédito", "credit"), ("Fondo de inversión", "invest")):
            con.execute("INSERT INTO accounts(name, kind) VALUES (?,?)", (name, kind))
        acc = {r["name"]: r["id"] for r in con.execute("SELECT id, name FROM accounts")}

        def tx(day: date, desc: str, amount: float, cat: str, account: str = "Cuenta de ahorros") -> None:
            h = hashlib.sha1(f"{account}|{day}|{desc}|{amount}".encode()).hexdigest()
            con.execute("INSERT OR IGNORE INTO transactions(account_id, date, description, amount, category, hash) VALUES (?,?,?,?,?,?)",
                        (acc[account], day.isoformat(), desc, amount, cat, h))

        for m in range(11):
            first = (TODAY.replace(day=1) - timedelta(days=30 * m)).replace(day=1)
            d = lambda k: first + timedelta(days=k)  # noqa: E731
            tx(d(14), "PAGO DE NOMINA EMPRESA DEMO", 7_200_000, "Salario")
            tx(d(15), "TRASLADO A FONDO DE INVERSION", -1_500_000, "Movimiento entre mis cuentas")
            tx(d(15), "ADICION", 1_500_000, "Movimiento entre mis cuentas", "Fondo de inversión")
            tx(d(2), "ARRIENDO", -1_800_000, "Vivienda")
            tx(d(4), "SUPERMERCADO EXITO", -420_000 - 15_000 * m, "Mercado")
            tx(d(9), "SUPERMERCADO D1", -180_000, "Mercado")
            tx(d(5), "NETFLIX.COM", -39_900, "Suscripciones y software")
            tx(d(6), "SPOTIFY", -17_900, "Suscripciones y software")
            tx(d(8), "UBER *TRIP", -48_000 - 4_000 * m, "Transporte y gasolina")
            tx(d(11), "TERPEL GASOLINA", -120_000, "Transporte y gasolina")
            tx(d(12), "RESTAURANTE EL CORRAL", -96_000, "Comida fuera")
            tx(d(18), "CINE COLOMBIA", -52_000, "Entretenimiento y salidas")
            tx(d(20), "FARMATODO", -64_000, "Salud y farmacia")
            tx(d(21), "COMPRA ONLINE AMAZON", -210_000 + 20_000 * m, "Compras online", "Tarjeta de crédito")
            tx(d(27), "ABONO INTERESES FONDO", 95_000 + 4_000 * m, "Intereses y rendimientos", "Fondo de inversión")
        finance.set_balance(con, "Cuenta de ahorros", 5_340_000, D(-1))
        finance.set_balance(con, "Tarjeta de crédito", -1_120_000, D(-1))
        finance.set_balance(con, "Fondo de inversión", 24_800_000, D(-1))
        # --- correo (todo ficticio)
        for i, (cat, sender, subject, needs) in enumerate((
            ("career", "jobalerts@jobs.example", "Senior Data Analyst — Acme Corp (Bucaramanga) · $9.000.000", 1),
            ("career", "jobalerts@jobs.example", "Analista de Planeación — Globex · remoto", 1),
            ("career", "talento@acme.example", "Acme: invitación a prueba técnica (plazo viernes)", 1),
            ("finance", "alertas@banco.example", "Extracto de septiembre disponible", 1),
            ("finance", "pagos@tarjeta.example", "Tu pago vence en 3 días", 1),
            ("study", "campus@universidad.example", "Entrega: taller 3 de optimización (jueves)", 1),
            ("security", "no-reply@accounts.example", "Alerta de seguridad: nuevo inicio de sesión", 1),
            ("human", "mama@familia.example", "Cena del domingo", 1),
            ("newsletter", "news@boletin.example", "Las 10 noticias de datos de la semana", 0),
            ("promo", "ofertas@tienda.example", "30% de descuento solo hoy", 0),
        )):
            con.execute(
                "INSERT INTO emails(account, message_id, folder, date, from_addr, from_name, subject, snippet, body_text, category, needs_claude, reason) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("alex@demo.example", f"demo-{i}", "INBOX", (TODAY - timedelta(days=i // 3)).isoformat() + "T09:00:00+00:00", sender, "", subject, "", "", cat, needs, "demo"))
        # --- un plan con cronograma
        outline = """## Semana 1 · Fundamentos
- Introducción al modelaje predictivo (video, 25 min)
- Lectura: sesgo y varianza (lectura, 30 min)
## Semana 2 · Regresión
- Regresión lineal y regularización (video, 40 min)
- Taller 1 (tarea, 90 min)
## Semana 3 · Clasificación
- Regresión logística (video, 35 min)
- Taller 2 (tarea, 90 min)
"""
        plans.create_plan(con, "Curso de Machine Learning", outline, kind="curso", start=D(1), hours_per_week=6, days="lun,mar,mie,jue,vie", project="Maestría: curso final")
        for lvl, title in (("IMPORTANT", "Entregar el taller 3 de optimización · hoy"), ("INFO", "2 ofertas nuevas coinciden con tu perfil")):
            con.execute("INSERT INTO alerts(level, title, detail, source) VALUES (?,?,?,?)", (lvl, title, "", "rule"))
    print("demo lista en", DEMO)


if __name__ == "__main__":
    seed()
