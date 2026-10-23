"""Preguntas a la IA sobre tus planes y notas. Se guarda cada pregunta con el contexto recuperado (búsqueda de texto local).
- IA local (Ollama): responde al momento, pero tarda 1–3 min en este equipo.
- IA virtual (Claude Code): responde cuando abres una sesión ("responde mis preguntas pendientes") o en la tarea programada.
Sin APIs de modelos externos: nada sale del PC salvo lo que Claude Code ya procesa en tu sesión."""
import json
import re
import unicodedata

import typer

from . import db
from .util import JSON, emit

app = typer.Typer(help="Preguntas a la IA (local o Claude)", no_args_is_help=True)
STOP = set("como cual cuales cuando donde porque para que los las una uno unos unas del con por sobre entre esto esta este esos esas mas menos muy sin the and what how why del".split())


def _words(q: str) -> list[str]:
    q = unicodedata.normalize("NFKD", q).encode("ascii", "ignore").decode().lower()
    return [w for w in re.findall(r"[a-z0-9]{4,}", q) if w not in STOP][:8]


def retrieve(con, question: str, scope: str = "general", limit: int = 6) -> list[dict]:
    """Fragmentos relevantes: ítems del plan (si aplica) y notas indexadas."""
    ctx: list[dict] = []
    words = _words(question)
    if scope.startswith("plan:"):
        pid = int(scope.split(":")[1])
        p = con.execute("SELECT title, hours_per_week, start_date, end_date FROM plans WHERE id=?", (pid,)).fetchone()
        if p:
            ctx.append({"source": "plan", "text": f"Plan '{p['title']}': {p['hours_per_week']} h/semana, del {p['start_date']} al {p['end_date']}."})
            like = " OR ".join(["lower(i.title) LIKE ?"] * len(words)) or "1=0"
            rows = con.execute(f"SELECT i.title, i.kind, i.scheduled_date, i.status, s.title AS stage FROM plan_items i JOIN plan_stages s ON s.id=i.stage_id "
                               f"WHERE i.plan_id=? AND ({like}) LIMIT ?", (pid, *[f"%{w}%" for w in words], limit)).fetchall()
            ctx += [{"source": "plan", "text": f"[{r['stage']}] {r['title']} ({r['kind']}, {r['scheduled_date']}, {r['status']})"} for r in rows]
    if words:
        try:
            for r in con.execute("SELECT n.path, n.title, snippet(notes_fts, 2, '', '', ' … ', 40) AS s FROM notes_fts f JOIN notes n ON n.id=f.rowid "
                                 "WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?", (" OR ".join(words), limit)):
                ctx.append({"source": f"nota:{r['path']}", "text": f"{r['title']}: {r['s']}"})
        except Exception:
            pass
    return ctx[: limit + 2]


def add_question(con, question: str, scope: str = "general") -> int:
    ctx = retrieve(con, question, scope)
    cur = con.execute("INSERT INTO questions(question, scope, context) VALUES (?,?,?)", (question.strip(), scope, json.dumps(ctx, ensure_ascii=False)))
    db.audit(con, "ask.add", "WRITE", f"question:{cur.lastrowid}", detail=question[:120])
    return cur.lastrowid


def save_answer(con, qid: int, answer: str, by: str) -> None:
    con.execute("UPDATE questions SET answer=?, answered_by=?, status='answered', answered_at=datetime('now') WHERE id=?", (answer.strip(), by, qid))
    db.audit(con, "ask.answer", "WRITE", f"question:{qid}", detail=by)
    if by != "local":  # la respuesta de Claude llega sola al celular
        from . import notify
        q = con.execute("SELECT question FROM questions WHERE id=?", (qid,)).fetchone()
        notify.push_text(f"💬 Respuesta a tu pregunta #{qid}: {q['question'][:120]}\n\n{answer.strip()[:3300]}")


def answer_local(qid: int) -> str:
    """Responde con Ollama usando el contexto recuperado (1–3 min en este equipo; no bloquea el portal si se lanza en un hilo)."""
    from . import ai
    with db.session() as con:
        q = con.execute("SELECT question, context FROM questions WHERE id=?", (qid,)).fetchone()
    ctx = "\n".join(f"- {c['text']}" for c in json.loads(q["context"] or "[]")) or "(sin contexto encontrado)"
    prompt = ("Eres un asistente de estudio y organización. Responde en español, breve y práctico, usando SOLO el contexto si alcanza; "
              f"si falta información dilo.\n\nContexto:\n{ctx}\n\nPregunta: {q['question']}")
    try:
        answer = ai.generate(prompt, timeout=600, max_tokens=1800)
    except Exception as e:
        answer = f"No pude responder con la IA local ({type(e).__name__}). Déjala para Claude."
    with db.session() as con:
        if answer and not answer.startswith("No pude"):
            save_answer(con, qid, answer, "local")
    return answer


@app.command("add")
def add_cmd(question: str, scope: str = typer.Option("general", help="general | plan:ID")) -> None:
    with db.session() as con:
        typer.echo(f"pregunta #{add_question(con, question, scope)}")


@app.command("list")
def list_cmd(pending: bool = False, as_json: bool = JSON) -> None:
    with db.session() as con:
        rows = con.execute("SELECT id, status, scope, answered_by, question FROM questions" + (" WHERE status='pending'" if pending else "") + " ORDER BY id DESC LIMIT 50").fetchall()
        emit(rows, as_json)


@app.command("show")
def show_cmd(qid: int) -> None:
    """Pregunta con su contexto (lo que Claude lee para responder)."""
    with db.session() as con:
        q = con.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
    typer.echo(json.dumps(dict(q), ensure_ascii=False, indent=2))


@app.command("answer")
def answer_cmd(qid: int, answer: str, by: str = "claude") -> None:
    with db.session() as con:
        save_answer(con, qid, answer, by)
    typer.echo("respondida")


@app.command("local")
def local_cmd(qid: int) -> None:
    typer.echo(answer_local(qid))
