import email
import email.policy
import mailbox
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def runner(tmp_path, monkeypatch):
    monkeypatch.setenv("POS_DB", str(tmp_path / "pos.db"))
    monkeypatch.setenv("POS_HOME", str(tmp_path))
    for m in [m for m in sys.modules if m == "pos" or m.startswith("pos.")]:
        del sys.modules[m]
    from pos.cli import app
    r = CliRunner()
    assert r.invoke(app, ["init"]).exit_code == 0
    return lambda *a: r.invoke(app, list(a))


def test_task_flow_and_today(runner):
    assert runner("project", "add", "Tesis", "--entity", "personal").exit_code == 0
    assert runner("task", "add", "Escribir intro", "--project", "Tesis", "--due", "2000-01-01").exit_code == 0
    out = runner("today").stdout
    assert "VENCIDAS: 1" in out and "Escribir intro" in out
    assert runner("task", "done", "1").exit_code == 0
    assert "VENCIDAS: 0" in runner("today").stdout


def test_inbox_to_task_and_audit(runner):
    runner("inbox", "add", "Llamar al banco")
    assert "Llamar al banco" in runner("inbox", "list").stdout
    assert runner("inbox", "resolve", "1", "--as", "task").exit_code == 0
    assert "Llamar al banco" in runner("task", "list").stdout
    assert "inbox.resolve" in runner("audit", "tail").stdout


def test_job_wrapper_records_failure(runner, monkeypatch):
    from pos import jobs
    monkeypatch.setattr("pos.notify.subprocess.run", lambda *a, **k: None)

    @jobs.job("boom")
    def boom():
        raise RuntimeError("falla de prueba")

    ok, msg = jobs.run_job("boom")
    assert not ok and "falla de prueba" in msg
    assert "boom" in runner("today").stdout


def test_mail_prefilter():
    from pos.mail import classify

    def m(frm, subj, **h):
        msg = email.message.EmailMessage()
        msg["From"], msg["Subject"] = frm, subj
        for k, v in h.items():
            msg[k.replace("_", "-")] = v
        return msg
    assert classify(m("Ana <ana@x.com>", "¿Almorzamos mañana?"))[:2] == ("human", 1)
    assert classify(m("Tienda <news@tienda.com>", "50% off hoy", List_Unsubscribe="<x>"))[:2] == ("promo", 0)
    assert classify(m("Banco <a@banco.com>", "Tu factura vence pronto"))[:2] == ("finance", 1)
    assert classify(m("Samsung <promo@s.com>", "Compra en cuotas y paga menos", List_Unsubscribe="<x>"))[:2] == ("newsletter", 0)  # publicidad, no cobro
    assert classify(m("G <no-reply@g.com>", "Alerta de seguridad crítica"))[:2] == ("security", 1)
    assert classify(m("G <no-reply@g.com>", "Tu código de verificación"))[:2] == ("security", 0)


def test_mail_ingest_from_mbox_and_digest(runner, tmp_path, monkeypatch):
    profile = tmp_path / "Profiles" / "abc.pos-mail"
    acc = profile / "ImapMail" / "imap.gmail.com"
    acc.mkdir(parents=True)
    box = mailbox.mbox(acc / "INBOX")
    for i, (frm, subj) in enumerate([("Ana <ana@x.com>", "Hola"), ("N <news@n.com>", "Semana")]):
        msg = email.message.EmailMessage()
        msg["From"], msg["Subject"], msg["Message-ID"] = frm, subj, f"<{i}@t>"
        msg["Date"] = email.utils.formatdate(localtime=False)
        if i:
            msg["List-Unsubscribe"] = "<u>"
        msg.set_content("cuerpo " + subj)
        box.add(msg)
    box.flush()
    (profile / "prefs.js").write_text(
        f'user_pref("mail.server.server1.directory", "{str(acc).replace(chr(92), chr(92) * 2)}");\n'
        'user_pref("mail.server.server1.userName", "yo%40gmail.com");\n', encoding="utf-8")
    from pos import mail
    monkeypatch.setattr(mail, "PROFILES_DIR", tmp_path / "Profiles")
    assert "nuevos=2" in mail.ingest()
    assert "nuevos=0" in mail.ingest()  # idempotente
    out = runner("mail", "digest").stdout
    assert "yo@gmail.com" in out and "Hola" in out and "Semana" not in out
    assert "Hola" in runner("mail", "search", "cuerpo").stdout
