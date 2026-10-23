# ARCHITECTURE

Sistema operativo personal **local-first**: un dueño, un portátil (Windows 11), datos en formatos abiertos, Claude Code como cerebro. Decisiones y alternativas: `DECISIONS.md`. Relaciones: `SYSTEM_MAP.md`. Qué significa cada término: `DICTIONARY.md`.

## En una imagen
```
TÚ ──► Portal (PC) · Telegram (celular) · Claude (esta conversación / Claude Code) · doble clic
                │                         │                       │
                ▼                         ▼                       ▼
        ┌───────────────  SQLite data\pos.db  +  notas Markdown data\knowledge  ───────────────┐
        │  tareas · proyectos · planes · personas · documentos · finanzas · correo (resumen)   │
        └──────────────▲──────────────────────────────────▲────────────────────────────────────┘
                       │ escriben / leen                   │ leen
        Jobs (Programador de Windows, con el PC encendido y tu sesión abierta)
        reminders → ALERTAS · briefing · mail-ingest · telegram-capture · backup · cleanup · health …
                       │
                       ▼
        Alertas ──► Portal (Hoy/Sistema) · aviso de Windows · Telegram   (+ Google Calendar en la nube)
```

## Capas
1. **Datos:** SQLite `data\pos.db` (estructurado) y Markdown+Git `data\knowledge\` (notas). Se autorreparan: cualquier proceso que abre la base aplica el esquema y las migraciones.
2. **Automatización (jobs):** el Programador de Windows lanza `run_job.pyw <job>`; cada ejecución queda en `job_runs` y un fallo genera alerta (`JOBS.md`).
3. **Motor de alertas:** lee tus datos cada hora y decide qué merece avisarte (ver abajo).
4. **Interfaces:** portal (PC), Telegram (celular), Claude (razonar, planificar, responder), Google Calendar (avisos que viven en la nube).
5. **Herramientas:** FFmpeg, Pillow, faster-whisper, PyMuPDF, restic, Thunderbird, Shotcut, GIMP; Ollama solo para visión/OCR (`TOOLS.md`).

## Alertas: de dónde salen y cómo te llegan sin pedirlas
**Origen: tus datos.** El job `reminders` (cada 10 minutos) recorre la base con reglas fijas y crea alertas:
| Regla | Nivel |
|---|---|
| Tarea vencida (prioridad 1 = crítica; más de 5 se agrupan en una) | importante / crítica |
| Documento por vencer (60 d aviso · 30 d importante · 7 d o vencido crítica) | según cercanía |
| Suscripción que se cobra en ≤ 3 días | importante |
| Cumpleaños de otras personas: hoy (importante) o en 7 días (aviso) | importante / aviso |
| **Familia y pareja** (cumpleaños, aniversario, San Valentín, Amor y Amistad, Día de la Madre/Padre/Mujer, regalos de Navidad): a 30 días, 15 días y el mismo día | importante |
| Tarea de prioridad 1–2 o con etiqueta `pago` que vence hoy o mañana | importante |
| **Bloque de tiempo** que empieza en menos de 15 minutos (con botones ✅ Hecho / ⏰ Mañana en Telegram) | importante |
| Plan atrasado (> 3 ítems importante; 1–3 aviso) | importante / aviso |
| Postulación con siguiente paso hoy, mañana o vencido | importante |
| Salud: copia de seguridad muy vieja, disco lleno… | crítica (avisos solo informativos) |
| Correo de seguridad sin revisar | crítica |
| Racha de hábito ≥ 3 días en riesgo (después de las 20:00) | importante |
| Correos, inbox o preguntas acumulados | aviso |
Umbrales editables en `config\alerts.json`. Una alerta **no se repite** (clave estable) y **se archiva sola** cuando el dato deja de cumplirla (haces la tarea, renuevas el documento).

**Entrega (según nivel):**
- *Aviso:* solo el portal (Hoy y Sistema).
- *Importante:* portal + aviso de Windows + mensaje de Telegram.
- *Crítica:* igual, y suena aunque sea de madrugada.
- **Horas de silencio 22:00–07:00:** lo importante espera y se envía en cuanto llegue la mañana; lo crítico no espera.
- **Resúmenes que llegan solos a Telegram:** briefing 7:00, revisión de la noche 21:00, revisión semanal domingo 19:00; y la respuesta de Claude cuando contesta una pregunta tuya.

**Actuar sobre la alerta sin abrir el PC (Telegram):** `/alertas` · `/ok N` archiva · `/hecho N` completa la tarea · `/pospon N mañana` la mueve · `/hoy` · `/tareas` · `/plan` · `/buscar` · `/pregunta`. Cualquier otro mensaje (texto o nota de voz) se convierte en una captura del inbox.

## Cómo hablas con el sistema
| Para… | Usa |
|---|---|
| Ver y editar todo con calma | Portal en el PC (con PIN) |
| Que te avise, consultar rápido, capturar ideas, marcar hecho | Telegram |
| Razonar, planificar, resumir, preguntar de verdad | Claude (esta conversación o Claude Code en el PC) |
| Dejar una pregunta para después | «Preguntar a la IA» o `/pregunta`: la responde la IA local (lenta) o Claude en su siguiente sesión, y te llega por Telegram |
| Cosas de máquina (backups, editores) | Doble clic / botones del portal |

## Dónde vive todo y qué necesita estar encendido
- **En tu portátil:** la base de datos, las notas, el portal, los jobs, las copias preparadas. **Necesitan el portátil encendido y con tu sesión abierta** (los jobs corren «solo con la sesión iniciada»).
- **En la nube (siguen funcionando con el PC apagado):** Gmail/Hotmail, Google Calendar, Telegram (los mensajes esperan hasta 24 h) y la copia cifrada en OneDrive.

### Qué pasa si…
| Situación | Efecto | Qué lo compensa |
|---|---|---|
| **Portátil suspendido** (tapa cerrada) | No corren jobs ni llegan alertas de Telegram; Telegram guarda tus mensajes | Al despertar corre lo que se perdió (`StartWhenAvailable`); briefing y revisión de la noche pueden despertarlo si activas los temporizadores de reactivación (paso opcional) |
| **Portátil apagado** | Nada del PC funciona: ni portal, ni jobs, ni alertas nuevas (la app del celular muestra la última copia guardada de las páginas de consulta) | Google Calendar avisa lo crítico igual (vencimientos, cumpleaños, cobros, entrevistas, tareas urgentes — skill `calendar-sync`); al encender, todo se pone al día en minutos sin duplicar alertas |
| **Sin internet** | Portal y alertas locales siguen; no llega Telegram ni correo | Las alertas quedan pendientes y se envían al volver la conexión |
| **Thunderbird cerrado** | No entran correos nuevos al resumen | Paso opcional: arranque automático minimizado con Windows |
| **Disco externo desconectado** | Solo falta la copia mensual/trimestral | La copia semanal en C: y la cifrada en OneDrive siguen; la salud avisa si pasan 35 días |
| **Se rompe el portátil** | Pierdes el equipo, no los datos | Copia cifrada en OneDrive (esencial) + disco externo; se restaura con `RECOVERY.md`; correo, calendario y Telegram están en la nube |
| **Se cae el portal** | Los jobs y las alertas siguen | El Programador lo reinicia; también al iniciar sesión |
| **Ollama apagado** | Solo se pierden OCR de imágenes y respuestas con IA local | Claude responde las preguntas |
| **Claude Code cerrado** | No se responden preguntas ni se sincroniza el calendario | Se retoma en la siguiente sesión o corrida programada; nada se pierde |

## Principios aplicados
- **Claude no hace trabajo repetitivo:** captura, clasificación, filtrado de correo, alertas, briefings, backups, limpieza y monitoreo son scripts.
- **Una sola puerta de escritura:** `pos.services`/módulos; todo auditable.
- **Cero servicios extra:** sin Docker ni servidores; el portal es un proceso Python local.
- **Seguridad por capas** (`SECURITY.md`) y **orden obligatorio** (job `cleanup`, `SYSTEM_CLEANUP.md`).

## Estructura del repo
```
PERSONAL_OS/
  README.md ARCHITECTURE.md SYSTEM_MAP.md DICTIONARY.md TOOLS.md JOBS.md SKILLS.md MCP.md AGENTS.md
  DATA_ARCHITECTURE.md SECURITY.md BACKUP.md RECOVERY.md DECISIONS.md CHANGELOG.md ROADMAP.md
  SYSTEM_CLEANUP.md CLAUDE.md
  scripts/     pos.cmd, Backup-*.cmd, Correo.cmd, run_job.pyw, Captura.pyw, pos/ (código), tests/
  portal/      app.py, serve.pyw, templates/, static/
  automation/  Register-Jobs.ps1, Register-Shortcuts.ps1, Install-Editors-Admin.ps1
  config/      backup.json, y opcionales: alerts.json, portal.json, mail_rules.json, money_rules.json, ai.json
  .claude/     settings.json, skills/
  data/        (fuera de Git) pos.db, knowledge/, briefings/, logs/
  archive/     investigación de Fase 0
```
