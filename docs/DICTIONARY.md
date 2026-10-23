# DICTIONARY — Diccionario oficial

Única fuente de definiciones. Si un término cambia, se cambia aquí.

## Conceptos
- **Personal OS** — Sistema operativo personal local-first que gestiona la vida del usuario desde el computador.
- **Claude Code** — Orquestador principal (N4). Razona, planifica y coordina; no hace trabajo repetitivo.
- **Ollama** — Motor local de modelos de IA (N3). Solo se usa donde un script no basta.
- **Job** — Proceso automatizado que corre sin Claude Code (N2).
- **Skill** — Procedimiento reutilizable para Claude Code.
- **MCP** — Protocolo/capa de comunicación entre Claude Code y herramientas.
- **Agente** — Subagente especializado; solo existe si Claude Code no puede resolver la tarea sin él.
- **Knowledge Base** — Sistema de conocimiento persistente (Markdown + Git; formatos abiertos).
- **Inbox** — Entrada temporal de información aún no procesada. Se captura primero, se clasifica después.
- **Portal** — Dashboard local; muestra *qué está pasando*.
- **Single Source of Truth (SSOT)** — Lugar único donde vive un dato; lo demás es DERIVED o CACHE/INDEX.
- **Local-first** — Los datos viven en el equipo del usuario en formatos abiertos; la nube es opcional.

- **`pos`** — CLI del sistema (`scripts\pos.cmd`): única puerta de escritura; registra en `audit_log`.
- **Prefiltro (de correo)** — Reglas deterministas que clasifican cada correo; solo `needs_claude=1` llega a Claude.
- **Colector (de correo)** — Thunderbird con perfil `pos-mail`: descarga los buzones de todas las cuentas; `pos` los lee sin modificarlos.
- **Backup esencial** — Copia pequeña (≤1 GB) preparada en C: y trasladada al disco externo con doble clic.
- **Backup completo** — Copia de todo el PC al disco externo, bajo demanda e incremental.
- **Recurrencia** — Tarea que al completarse crea su siguiente ocurrencia (`--repeat monthly` …).
- **`cleanup`** — Job semanal que borra lo regenerable y reporta el desorden.
- **`entity`** — Columna que separa lo `personal` de lo `business`.

- **Portal** — Página local (`127.0.0.1:8765`) que muestra qué está pasando y permite actuar; usa la misma capa `services` que la CLI.
- **Triage** — Clasificación determinista del inbox (tarea/nota/descarte, fecha, proyecto); deja una sugerencia, no decide.
- **Briefing** — Resumen de mañana/noche/semana generado por script en `data\briefings\`.
- **Drop folder (carpeta de arrastrar y soltar)** — Carpeta de `PERSONAL_OS-Media` cuyo nombre es una acción; `media-watch` procesa lo que caiga.
- **Vault** — Carpeta `data\knowledge\` de notas Markdown con su propio Git.

- **Plan** — Curso, proyecto o meta con etapas e ítems que tienen duración y fecha; el cronograma se calcula solo (horas por semana y días de trabajo).
- **Etapa / Ítem** — Bloque de un plan (módulo, semana) y su unidad de trabajo (lección, quiz, práctica, entrega).
- **Etiqueta (tag)** — Palabra clave en minúsculas (`coursera`, `negocio`) para agrupar tareas, proyectos, planes y notas.
- **Doble candado** — Pedir el PIN otra vez para Finanzas, Correo y Empleo pasado un rato.
- **Alcance remoto** — Lista de secciones que sí se abren cuando se accede al portal desde fuera del PC.
- **Pregunta a la IA** — Pregunta guardada con contexto recuperado; la responde la IA local al momento o Claude en su próxima sesión.

- **Alerta** — Aviso que nace de tus datos (tarea vencida, documento por vencer…). Niveles: aviso (INFO), importante, crítica. No se repite y se archiva sola cuando ya no aplica.
- **Horas de silencio** — 22:00–07:00: los avisos importantes esperan; los críticos suenan siempre.
- **Primeros pasos** — Lista guiada del portal con lo que solo el usuario puede hacer (PIN, inicios de sesión, permisos).
- **Copia cifrada (offsite)** — Copia de la copia esencial en OneDrive, cifrada antes de subir.

## Niveles de arquitectura
- **N1** herramientas determinísticas · **N2** automatizaciones persistentes (jobs) · **N3** IA local · **N4** Claude Code.

## Estados oficiales
- **ACTIVE** parte de la arquitectura actual · **CANDIDATE** en evaluación · **PENDING_DECISION** requiere decisión humana · **EXPERIMENTAL** en prueba · **ARCHIVED** solo histórico · **REJECTED** evaluado y descartado · **REMOVED** eliminado.

## Permisos de acción
- **READ** leer · **WRITE** escribir datos del sistema · **LOCAL_ACTION** ejecutar algo en el equipo · **EXTERNAL_ACTION** afecta a terceros/servicios externos · **SENSITIVE_ACTION** dinero, envíos, borrados, identidad — siempre requiere aprobación.

## Notificaciones
- **INFO** · **IMPORTANT** · **CRITICAL**.

## Fases
- **Fase 0** investigación (sin instalar) … **Fase 13** hardening y consolidación. Detalle en ROADMAP.md.

## Abreviaturas
- **SSOT** Single Source of Truth · **KB** Knowledge Base · **FTS** Full-Text Search.

## Fechas y círculo cercano
- **Círculo** (`people.circle`): `familia`, `pareja` u `otros`. Familia y pareja avisan a **30 y 15 días** (y el mismo día); otros a 7 días.
- **Regla de fecha** (`special_dates.rule`): `MM-DD` (fija) o `N:D:M` = el N-ésimo día de la semana D (lunes=0…domingo=6) del mes M; p. ej. `2:6:5` = segundo domingo de mayo.

## Gestor de tareas
- **Bloque de tiempo:** tarea con día (`scheduled_date`), hora (`start_time`) y duración (`est_minutes`); es lo que se ve en el calendario y se sincroniza con Google.
- **Capacidad:** horas de tareas realistas por día (`config\planner.json`, 5 h). **Regla 1-3-5:** 1 grande, 3 medianas, 5 pequeñas.
- **Captura rápida:** una línea en lenguaje natural → tarea con fecha, hora, duración, prioridad, proyecto, etiquetas y repetición (`pos.quick`).
- **Agenda unificada:** todo lo fechado en un solo lugar (`pos.agenda`). **Copia local de Google:** tabla `gcal_events`.
