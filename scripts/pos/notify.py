"""Alertas y notificaciones por niveles.
  INFO       → solo portal (Hoy/Sistema).
  IMPORTANT  → portal + aviso de Windows + Telegram (si está configurado), salvo en horas de silencio.
  CRITICAL   → siempre, incluso de madrugada.
Las alertas con `key` no se repiten; las de regla (`source='rule'`) las archiva solo el motor cuando ya no aplican."""
import os
import subprocess
from datetime import datetime

from . import db

QUIET_FROM, QUIET_TO = 22, 7  # horas de silencio: no se envían avisos que no sean CRÍTICOS

_TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$n = $t.GetElementsByTagName('text')
$n.Item(0).AppendChild($t.CreateTextNode($env:POS_TOAST_TITLE)) | Out-Null
$n.Item(1).AppendChild($t.CreateTextNode($env:POS_TOAST_BODY)) | Out-Null
$id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""


def quiet_now(now: datetime | None = None) -> bool:
    h = (now or datetime.now()).hour
    return h >= QUIET_FROM or h < QUIET_TO


def _deliver(level: str, title: str, detail: str, key: str | None = None, alert_id: int | None = None) -> bool:
    """Aviso de Windows + Telegram. Devuelve True si se intentó entregar (en tests y ejecuciones en seco no hace nada)."""
    if os.environ.get("POS_NO_NOTIFY"):
        return False
    try:
        from . import telegram
        from . import tgfmt
        telegram.send(tgfmt.alert(level, title, detail), tgfmt.buttons(key, alert_id))  # al celular si está configurado
    except Exception:
        pass
    try:
        env = {**os.environ, "POS_TOAST_TITLE": f"[{level}] {title}", "POS_TOAST_BODY": detail[:200] or title}
        subprocess.run(["powershell", "-NoProfile", "-Command", _TOAST], env=env, timeout=20, capture_output=True, check=False)
    except Exception:
        pass  # la alerta ya está en la base; el aviso es solo cortesía
    return True


def push_text(text: str) -> bool:
    """Mensaje libre al celular (briefings, respuestas de Claude). No hace nada en tests ni si Telegram no está configurado."""
    if os.environ.get("POS_NO_NOTIFY"):
        return False
    try:
        from . import telegram
        return telegram.send(text)
    except Exception:
        return False


def toast(title: str, body: str = "") -> bool:
    """Solo aviso de Windows (sin crear alerta ni mensaje de Telegram): para resúmenes que no son problemas."""
    if os.environ.get("POS_NO_NOTIFY") or quiet_now():
        return False
    try:
        env = {**os.environ, "POS_TOAST_TITLE": title, "POS_TOAST_BODY": body[:200] or title}
        subprocess.run(["powershell", "-NoProfile", "-Command", _TOAST], env=env, timeout=20, capture_output=True, check=False)
        return True
    except Exception:
        return False


def flush_pending(now: datetime | None = None) -> int:
    """Envía las alertas IMPORTANT/CRITICAL aún no enviadas (respetando las horas de silencio)."""
    sent = 0
    with db.session() as con:
        rows = con.execute("SELECT id, level, title, detail, key FROM alerts WHERE pushed=0 AND acknowledged=0 AND level!='INFO' ORDER BY id").fetchall()
        for r in rows:
            if quiet_now(now) and r["level"] != "CRITICAL":
                continue
            if _deliver(r["level"], r["title"], r["detail"] or "", r["key"], r["id"]):
                sent += 1
            con.execute("UPDATE alerts SET pushed=1 WHERE id=?", (r["id"],))
    return sent


def alert(level: str, title: str, detail: str = "", key: str | None = None, source: str = "system", link: str | None = None,
          now: datetime | None = None) -> bool:
    """Registra una alerta. Con `key`, si ya existe (aunque esté archivada) no se repite. Devuelve True si es nueva."""
    with db.session() as con:
        cur = con.execute("INSERT OR IGNORE INTO alerts(level, title, detail, key, source, link, pushed) VALUES (?,?,?,?,?,?,?)",
                          (level, title, detail[:500], key, source, link, 1 if level == "INFO" else 0))
        new = bool(cur.rowcount)
    if new and level != "INFO":
        flush_pending(now)
    return new
