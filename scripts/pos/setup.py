"""Primeros pasos: lo que solo el usuario puede hacer (contraseñas, inicios de sesión, permisos), guiado y con estado real.
Cada paso se marca solo cuando los datos lo confirman; cada acción abre el programa o el permiso que hace falta."""
import os
import subprocess
import time
from pathlib import Path

import typer

from . import db

app = typer.Typer(help="Primeros pasos guiados", no_args_is_help=False, invoke_without_command=True)
PROGRAMS = db.LOCALAPPDATA / "Programs"
THUNDERBIRD = PROGRAMS / "Thunderbird" / "thunderbird.exe"
STARTUP = db.APPDATA / "Microsoft/Windows/Start Menu/Programs/Startup" / "PERSONAL_OS Correo.lnk"
ADMIN_SCRIPT = db.ROOT / "automation" / "Install-Editors-Admin.ps1"
MANUAL_FLAGS = ("backupkey",)  # pasos que solo el usuario puede confirmar
_cache: dict = {"t": 0.0, "v": None}


def _flag(con, key: str) -> bool:
    r = con.execute("SELECT value FROM meta WHERE key=?", (f"setup:{key}",)).fetchone()
    return bool(r and r["value"] == "1")


def set_flag(key: str, value: bool = True) -> None:
    if key not in MANUAL_FLAGS:
        raise ValueError("paso no confirmable a mano")
    with db.session() as con:
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (f"setup:{key}", "1" if value else "0"))
        db.audit(con, "setup.flag", "WRITE", key, detail="confirmado" if value else "desmarcado")
    _cache["t"] = 0


def wake_timers_enabled() -> bool:
    """¿Permite Windows que una tarea programada despierte el equipo suspendido? (lectura, no se modifica nada)."""
    try:
        o = subprocess.run(["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", "RTCWAKE"], capture_output=True, text=True, timeout=15).stdout
        import re
        vals = re.findall(r"0x[0-9a-fA-F]{8}", o)[-2:]  # corriente y batería
        return bool(vals) and all(int(v, 16) in (1, 2) for v in vals)
    except Exception:
        return False


def steps(force: bool = False) -> list[dict]:
    """Lista de pasos con su estado real. Cacheada 20 s (se consulta en cada página)."""
    if not force and _cache["v"] is not None and time.time() - _cache["t"] < 20:
        return _cache["v"]
    from . import mail, media, plans, portal_security as sec, telegram
    profile = mail._find_profile()
    with db.session() as con:
        n = lambda q: con.execute(q).fetchone()[0]
        editors = {e["key"]: bool(e["path"]) for e in media.editors()}
        out = [
            dict(key="pin", title="Crear el PIN del portal", done=sec.pin_is_set(), optional=False, action="link", url="/setup",
                 button="Crear PIN", how="Escribe un PIN de al menos 6 caracteres. Se guarda cifrado en Windows, nunca en un archivo."),
            dict(key="mail", title="Conectar tus cuentas de correo (Gmail y Hotmail)", done=bool(profile and mail._account_map(profile)), optional=False,
                 action="post", url="/start/open-mail", button="Abrir Thunderbird",
                 how="Se abre Thunderbird con el perfil dedicado. Agrega cada cuenta con tu inicio de sesión normal y déjalo sincronizar unos minutos. El sistema solo lee; no envía ni borra."),
            dict(key="mail-auto", title="Que el correo se sincronice solo al iniciar Windows", done=STARTUP.exists(), optional=True,
                 action="post", url="/start/mail-autostart", button="Activar",
                 how="Deja Thunderbird abierto y minimizado al encender el PC, para que los correos lleguen al portal sin que hagas nada."),
            dict(key="wake", title="Permitir que el portátil se despierte solo a las 7:00 para tu briefing", done=wake_timers_enabled(), optional=True,
                 action="none", url="", button="",
                 how="Es un ajuste de energía de Windows (te toca a ti). En PowerShell: powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1; powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1; powercfg /setactive SCHEME_CURRENT. Sirve si el portátil está SUSPENDIDO; si está apagado no hay forma de despertarlo."),
            dict(key="backupkey", title="Guardar la clave de los backups en Bitwarden", done=_flag(con, "backupkey"), optional=False,
                 action="confirm", url="/start/mark/backupkey", button="2. Ya la guardé",
                 how="La clave no se muestra en pantalla (seguridad). Paso a paso: (1) pulsa «1. Copiar la clave»; (2) abre Bitwarden (app o bitwarden.com) y entra con tu contraseña maestra; (3) botón «+ Nuevo» → «Nota segura» (o «Inicio de sesión»); (4) Nombre: Clave backups PERSONAL_OS; (5) en el cuadro «Notas» toca y pega con Ctrl+V; (6) Guardar; (7) vuelve aquí y pulsa «2. Ya la guardé». Sin esa clave no se puede restaurar ningún backup.",
                 pre=[dict(url="/start/copy-key", button="1. Copiar la clave")]),
            dict(key="backup", title="Primer backup esencial al disco externo",
                 done=bool(con.execute("SELECT 1 FROM job_runs WHERE job='backup-essential' AND status='ok' LIMIT 1").fetchone()), optional=False,
                 action="post", url="/start/open-backup", button="Abrir el backup",
                 how="Conecta el disco externo y pulsa el botón: se abre una ventana que pregunta cuál es el disco (solo la primera vez) y copia lo esencial en minutos."),
            dict(key="telegram", title="Conectar Telegram (alertas y captura desde el celular)", done=telegram.configured(), optional=False,
                 action="link", url="/start/telegram", button="Configurar",
                 how="Crea un bot con @BotFather en Telegram, pega aquí su token y escríbele /start. Después recibes las alertas importantes y puedes usar /hoy, /plan, /tareas."),
            dict(key="editors", title="Instalar Inkscape y Upscayl (editores de imagen)", done=editors.get("vector", False) and editors.get("mejorar", False), optional=True,
                 action="post", url="/start/install-editors", button="Instalar (pide permisos)",
                 how="Windows te pedirá permisos de administrador: acéptalos. Instala Inkscape (logos y vectores) y Upscayl (mejorar imágenes con IA)."),
            dict(key="plan", title="Cargar tu curso o proyecto en Planes", done=n("SELECT COUNT(*) FROM plans") > 0, optional=False,
                 action="link", url="/plans", button="Ir a Planes", how="Pega el temario del curso de Coursera y elige horas por semana: se arma el cronograma."),
            dict(key="data", title="Cargar tus datos: hábitos, documentos, cumpleaños", done=n("SELECT COUNT(*) FROM habits") > 0 and (n("SELECT COUNT(*) FROM documents") + n("SELECT COUNT(*) FROM people")) > 0,
                 optional=False, action="link", url="/life", button="Ir a Vida y documentos",
                 how="Con esto el motor de alertas te avisa de vencimientos, cumpleaños y rachas. Sin datos no hay nada que avisar."),
            dict(key="money", title="Importar tu primer extracto bancario (CSV)", done=n("SELECT COUNT(*) FROM transactions") > 0, optional=True,
                 action="link", url="/money", button="Ir a Finanzas", how="Descarga el CSV desde tu banco, crea la cuenta y súbelo. Se categorizan solos los movimientos conocidos."),
            dict(key="remote", title="Portal en el celular por red privada (Tailscale)", done=bool(sec.config()["allowed_hosts"]), optional=True,
                 action="none", url="", button="", how="Opcional. Instala Tailscale en PC y celular (tú creas la cuenta) y sigue SECURITY.md → Acceso desde el celular. Nunca se abre a internet."),
        ]
    _cache.update(t=time.time(), v=out)
    return out


def pending(required_only: bool = True) -> int:
    return sum(1 for s in steps() if not s["done"] and not (required_only and s["optional"]))


# ---------- acciones (solo desde el PC; el portal las bloquea en remoto) ----------
def _audit(action: str, target: str = "") -> None:
    with db.session() as con:
        db.audit(con, action, "LOCAL_ACTION", target)
    _cache["t"] = 0


def open_mail() -> str:
    if not THUNDERBIRD.exists():
        raise RuntimeError("Thunderbird no está instalado")
    from . import mail
    subprocess.Popen([str(THUNDERBIRD), "-profile", str(mail.MAIL_PROFILE)], close_fds=True)
    _audit("setup.open-mail")
    return "Thunderbird abierto: agrega tus cuentas"


def enable_mail_autostart() -> str:
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:POS_LNK);$s.TargetPath=$env:POS_EXE;$s.Arguments='-profile ' + [char]34 + $env:POS_PROFILE + [char]34;"
          "$s.WindowStyle=7;$s.Save()")  # 7 = minimizado
    from . import mail
    env = {**os.environ, "POS_LNK": str(STARTUP), "POS_EXE": str(THUNDERBIRD), "POS_PROFILE": str(mail.MAIL_PROFILE)}
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, check=True, timeout=30, capture_output=True)
    _audit("setup.mail-autostart", str(STARTUP))
    return "Listo: Thunderbird arrancará minimizado con Windows"


def copy_backup_key() -> str:
    """Copia la clave de los backups al portapapeles SIN mostrarla (para pegarla en Bitwarden). Queda auditado como acción sensible."""
    from . import backup
    subprocess.run(["clip"], input=backup._password().encode("utf-16-le"), check=True, timeout=15)
    with db.session() as con:
        db.audit(con, "backup.key.copy", "SENSITIVE_ACTION", "restic-password", detail="portapapeles, sin mostrar")
    return "Clave copiada al portapapeles: pégala en una nota de Bitwarden y luego copia otra cosa para borrarla"


def open_backup() -> str:
    os.startfile(str(db.ROOT / "scripts" / "Backup-Esencial.cmd"))  # noqa: S606 — abre la ventana del backup
    _audit("setup.open-backup")
    return "Se abrió la ventana del backup"


def install_editors() -> str:
    """Lanza el script de instalación con elevación: Windows mostrará su aviso de permisos al usuario."""
    arg = f"-NoProfile -ExecutionPolicy Bypass -File \"{ADMIN_SCRIPT}\""
    subprocess.Popen(["powershell", "-NoProfile", "-Command", f"Start-Process powershell -Verb RunAs -ArgumentList '{arg}'"], close_fds=True)
    _audit("setup.install-editors")
    return "Acepta el aviso de permisos de Windows para instalar los editores"


@app.callback()
def show(ctx: typer.Context) -> None:
    """Estado de los primeros pasos."""
    if ctx.invoked_subcommand:
        return
    todo = 0
    for s in steps(force=True):
        mark = "✔" if s["done"] else ("·" if s["optional"] else "✖")
        todo += (not s["done"] and not s["optional"])
        typer.echo(f"{mark} {s['title']}" + ("  (opcional)" if s["optional"] and not s["done"] else ""))
    typer.echo(f"\nPendientes obligatorios: {todo}. Abre el portal → Primeros pasos para hacerlos con un clic.")
