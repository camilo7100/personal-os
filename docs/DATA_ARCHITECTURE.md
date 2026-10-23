# DATA_ARCHITECTURE — dónde vive cada dato

Todo lo estructurado está en **un solo archivo** SQLite (`data\pos.db`); las notas, en Markdown (`data\knowledge\`). Nada se duplica: lo demás son vistas (portal, briefings, Telegram) o cachés.

## ¿Dónde se guarda…?
| Lo que quieres saber | Se guarda en | Lo ves en |
|---|---|---|
| **Cumpleaños** y personas | tabla `people` (`birthday` MM-DD, `circle` familia/pareja/otros, `last_contact`); fechas anuales en `special_dates`; ítems de Coursera en `external_items` | Vida y documentos · HOY (14 días) · briefing |
| **Fechas límite** y **bloques de tiempo** de tareas | `tasks.due_date`, `scheduled_date` (día de trabajo), `start_time` (HH:MM), `est_minutes`, `actual_minutes`, `parent_id` (subtareas), `someday` | HOY · Tareas · Calendario · Telegram `/tareas` `/dia` |
| **Eventos de Google Calendar** (copia local, solo lectura) | `gcal_events` (la rellena Claude: `pos calendar pull` / `pos agenda cache`) | Calendario · planificador |
| **Tiempo enfocado** (Pomodoro) | `task_time` + `tasks.actual_minutes` | Tarea · Proyectos · Revisión |
| **Saldos, patrimonio y metas de ahorro** | `balances`, `savings_goals` | Finanzas |
| **Extractos importados** (qué archivo, qué meses cubre cada cuenta, cortes de tarjeta) | `statement_files`, `statement_periods`, `card_statements`; los archivos originales en `data\finance\extractos\<banco>\` | Finanzas |
| **Vencimientos** (pasaporte, SOAT, cámara de comercio) | tabla `documents` (`expires_on`) | Vida y documentos · HOY · briefing |
| **Suscripciones y cobros** | tabla `subscriptions` (`next_date`) | Vida y documentos · Finanzas |
| **Trabajos / empleo** (vacantes, entrevistas) | tablas `companies` y `applications` (`next_step_date`) | Carrera · HOY |
| **Trabajo del negocio** | cualquier tabla con `entity='business'` | filtros «Negocio» |
| **Planes de trabajo** (cursos, proyectos) | `plans` → `plan_stages` → `plan_items` (fecha por ítem) | Planes (Gantt) · HOY · Telegram `/plan` |
| **Etiquetas** | columna `tags` en tareas, proyectos y planes (minúsculas, sin `#`) y en el frontmatter de las notas | Tareas (filtro y nube) · Proyectos · Planes |
| **Proyectos y objetivos** | `projects`, `goals` | Proyectos · Objetivos |
| **Hábitos** | `habits` + `habit_log` (una fila por día hecho) | HOY · Vida |
| **Inbox** (captura) | `inbox` (con sugerencia automática) | Inbox |
| **Correo** (todas las cuentas) | proveedor + Thunderbird; aquí solo metadatos y primeros 6000 caracteres en `emails` | Correo |
| **Finanzas** | `accounts`, `transactions`, `budgets` (importadas de CSV) | Finanzas |
| **Notas y conocimiento** | archivos `.md` en `data\knowledge\<área>\` (Git) + índice `notes` | Conocimiento · Estudio |
| **Tarjetas de repaso** | líneas `pregunta :: respuesta` dentro de las notas | mazo `.apkg` para Anki |
| **Preguntas a la IA** y sus respuestas | `questions` | Preguntar a la IA |
| **Eventos de calendario** | Google Calendar (fuente); el plan exporta `.ics` | Calendar (celular) |
| **Estado del sistema** | `job_runs`, `alerts`, `health`, `audit_log`, `repos` | Sistema · Automatizaciones · Desarrollo |

## Reglas
- **Fuente de verdad:** SQLite para lo estructurado, Markdown+Git para el conocimiento, el proveedor para correo y calendario, Bitwarden/Credential Manager para secretos. Los índices se regeneran.
- **Esquema:** `scripts\pos\schema.sql` (única definición; no se duplica aquí).
- **Backups:** `data\` va en el backup esencial (archivos > 25 MB excluidos). Detalle en `BACKUP.md`.
- **Retención automática (`cleanup`):** métricas 90 d, jobs 180 d, auditoría 400 d, correo masivo revisado 90 d.
- **Personal vs negocio:** columna `entity` (`personal`|`business`).
