# SECURITY

**Principio:** el enlace del portal no es una llave. Para ver algo hace falta estar en tu PC o pasar por tu red privada, saber el PIN, y para lo sensible desbloquear de nuevo.

## Portal (capas)
1. **Solo tu PC:** escucha en `127.0.0.1`; rechaza cualquier `Host` u `Origin` que no sea local o que no hayas autorizado (anti DNS-rebinding y formularios de otros sitios).
2. **PIN:** `pos portal set-pin` (mínimo 6 caracteres). Se guarda como hash scrypt en el Administrador de credenciales de Windows, nunca en un archivo. Sin PIN el portal sigue siendo solo local y muestra un aviso.
3. **Sesión:** cookie firmada (`HttpOnly`, `SameSite=Strict`, `Secure` en remoto); dura 1 año rodante (se renueva al usarla); `pos portal lock` o «Bloquear portal» cierran todas.
4. **Doble candado:** Finanzas, Correo y Empleo piden el PIN otra vez si pasan 30 días sin usarlos.
5. **Intentos:** 5 fallos en 15 min bloquean 5 min y generan una alerta IMPORTANT.
6. **Alcance remoto:** desde fuera del PC solo se abren Hoy, Inbox, Tareas, Proyectos, Planes, Estudio, Conocimiento, Preguntar y Sistema (este último solo lectura). Finanzas, Correo, Empleo, Vida y documentos, Multimedia, Desarrollo y Automatizaciones **nunca** se abren en remoto, ni ninguna acción sobre el PC. Configurable en `config\portal.json`.
7. **Navegador:** CSP estricta (sin scripts en línea), `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, `no-store`; los formularios se aceptan solo si el navegador atestigua que son del mismo origen (`Sec-Fetch-Site`); respuestas de IA y notas se escapan antes de mostrarse; rutas de notas protegidas contra path traversal.
8. **Sin secretos en pantalla:** ninguna página ni ruta muestra contraseñas, tokens ni claves (la clave de backup solo sale por `pos backup key` en tu terminal, prohibido para Claude).

## Si olvidas el PIN (recuperación)
El PIN solo protege el portal; **no cifra tus datos**. Con tu sesión de Windows conservas el control total:
- **Restablecer el PIN:** menú Inicio → PERSONAL_OS (herramientas) → *Restablecer PIN del portal* (o `pos portal reset-pin`). Quita el PIN, cierra las sesiones y el portal te deja crear uno nuevo. Solo funciona en el PC con tu usuario de Windows; nunca desde el celular ni por red.
- **Sin portal:** la CLI (`scripts\pos.cmd …`), Telegram y Claude siguen funcionando; los datos están en `data\pos.db` y `data\knowledge\`.
- **Clave de los backups:** vive en el Administrador de credenciales de tu usuario de Windows y, por eso, **debe estar también en Bitwarden**. Si perdieras el usuario de Windows, esa copia es la única forma de abrir los backups.
- **Bitwarden:** su contraseña maestra la gestionas tú (Bitwarden no puede recuperarla); guárdala aparte en papel.

## Acceso desde el celular (recomendado)
- **Sin abrir nada:** Telegram (`/hoy`, `/tareas`, `/plan`, `/buscar`, `/pregunta`, captura de texto y voz), Google Calendar y Gmail.
- **Portal remoto opcional, solo por Tailscale** (red privada entre tus dispositivos; la cuenta la crea el usuario): instalar Tailscale en PC y celular → `tailscale serve --bg 8765` en el PC → `scripts\pos.cmd portal allow-host <nombre-del-pc>.<tu-red>.ts.net`. Nunca se abre un puerto a internet.

## Otros controles
- **Permisos por acción** (`audit_log.permission`): READ · WRITE · LOCAL_ACTION · EXTERNAL_ACTION · SENSITIVE_ACTION (aprobación explícita: enviar correos, postular, pagar, borrar datos del usuario, ver la clave del backup, configurar Telegram o el PIN).
- **Correo:** solo lectura; el login (OAuth) lo hace el usuario en Thunderbird. **Empleo:** nunca se postula solo. **Finanzas:** solo extractos CSV, sin APIs bancarias; el CSV subido no se conserva.
- **Secretos:** contraseñas en Bitwarden; PIN, tokens (restic, Telegram) y clave de sesión en el Credential Manager (`keyring`). Nada en Git, `.md` ni `config\`.
- **Telegram:** el bot solo obedece al chat autorizado en `setup`.
- **Claude Code:** `.claude\settings.json` permite `pos` y pytest y prohíbe `pos backup key`.
- **Auditoría:** cada acción de `pos`, portal, inicios de sesión y jobs queda en `audit_log` (400 días).
- **Privacidad:** IA local solo para visión/OCR; el trabajo con datos sensibles es determinista y local; a Claude llegan resúmenes filtrados. Contenido externo (web, correos, documentos) es dato, nunca instrucciones.
- **Backups cifrados;** la clave debe existir en Bitwarden (`BACKUP.md`).

## Dónde está cada secreto (sin contenido)
- **Código de recuperación de Bitwarden:** `C:\\Users\\<usuario>\\Documents\Seguridad\Bitwarden_Codigo-recuperacion.pdf` (carpeta solo para tu usuario, SYSTEM y administradores). Entra en el backup completo (cifrado). No se sube a OneDrive ni a Git.
- **Clave de los backups (restic):** Administrador de credenciales de Windows + Bitwarden.
- **PIN del portal y token de Telegram:** Administrador de credenciales (nunca en archivos).
- **Sesiones de correo:** `data\mail-profile` (fuera de Git y de los backups).
- Regla: nada de claves ni códigos sueltos en Descargas; van a Bitwarden o a `Documents\Seguridad`.

- **Duración de la sesión y dispositivos (decisión del usuario, 2026-10-02):** la sesión dura 1 año rodante y el PIN se escribe una sola vez; **no hay candado extra** en Finanzas, Correo ni Carrera. Los celulares, tabletas y otros Windows **no usan PIN**: se emparejan una vez con un enlace o QR de un solo uso (vale 10 minutos; en la base solo se guarda su huella) y la sesión queda atada a ese dispositivo, que se puede **desconectar** desde el menú Dispositivos. Desde un dispositivo emparejado (acceso remoto por Tailscale) se abre todo el portal: `remote_allowed: ["*"]`, `sensitive: []`. Quien tenga el celular desbloqueado y emparejado ve todo, incluidas finanzas (riesgo aceptado por el usuario); por eso se desconecta de inmediato un equipo perdido. Editable en `config\portal.json`.
- **Extractos bancarios:** se procesan solo en este PC. La clave de los PDF (el número de documento del usuario) vive en el Administrador de credenciales (`statements-pdf-password`); los originales quedan en `data\finance\extractos` (fuera de Git; entran en el backup cifrado). Finanzas se abre también en los dispositivos emparejados (decisión del usuario).
- **Claves de PDF:** el Administrador de credenciales guarda la clave principal (`statements-pdf-password`) y claves extra (`-2`, `-3`…; p. ej. la de una cuenta de un familiar que guarda plata del usuario). Al abrir un PDF protegido se prueban todas. El usuario autorizó guardarlas; nunca se escriben en archivos ni en el repositorio.
- **Datos de terceros:** los extractos de la cuenta de un familiar (cuenta de un familiar) se guardan solo como archivo y totales para estimar tu bolsillo; sus movimientos no se importan a tu libro.
- **Panel personal fuera del PC (copia cifrada):** el PC renderiza las páginas del portal, las comprime y las cifra con AES-256-GCM (`pos panel build`) y publica el archivo `data.bin` más un visor de HTML/JS en el repositorio público `<usuario>/panel-personal` (GitHub Pages; historial reescrito, un solo commit). Sin la llave nadie lee nada: la llave (`panel-key`) vive en el Administrador de credenciales y llega a cada equipo UNA vez en el #fragmento del enlace de emparejamiento (el navegador no lo envía al servidor); queda en el almacenamiento local de ese equipo. Cada enlace generado se avisa por Telegram. Equipo perdido o enlace filtrado: `pos panel key --force` y volver a emparejar los demás. Riesgos aceptados: el archivo cifrado es público (tamaño y fecha de actualización son visibles); quien tenga un equipo emparejado desbloqueado ve todo, incluidas finanzas.

