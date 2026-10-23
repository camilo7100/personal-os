# JOBS — catálogo oficial (solo jobs ACTIVE)

Se ejecutan con `scripts\run_job.pyw <nombre>` desde el Programador de Windows (carpeta `\PERSONAL_OS\`, registro idempotente con `automation\Register-Jobs.ps1`, que también elimina jobs huérfanos). Cada ejecución queda en `job_runs`; un fallo genera alerta IMPORTANT (toast + Telegram si está configurado). Estado: `pos job list` o el portal (Automatizaciones). Errores detallados: `data\logs\<job>.log`. El horario real lo define el Programador; esta tabla es el resumen.

| Job | Propósito | Horario | Entrada → Salida |
|---|---|---|---|
| `health` | Métricas (disco, RAM, CPU, Ollama, edad y peso de backups) | cada hora | sistema → `health` |
| `mail-ingest` | Lee los buzones de Thunderbird (solo lectura), prefiltra y guarda metadatos | cada 30 min | perfil `data\mail-profile` → `emails` |
| `telegram-capture` | Trae mensajes del chat autorizado (texto y voz→texto local) al inbox | cada 2 min | Telegram → `inbox` (no hace nada sin configurar) |
| `media-watch` | Procesa lo soltado en las carpetas de `Documents\PERSONAL_OS-Media` | cada 10 min | carpetas → `Salida\` |
| `inbox-triage` | Sugiere tarea/nota/descarte, fecha y proyecto (determinista) | cada hora | `inbox` → `inbox.suggestion` |
| `reminders` | **Motor de alertas:** convierte tus datos (fechas, vencimientos, cumpleaños, planes, salud) en alertas sin repetir, archiva las resueltas y envía las importantes respetando horas de silencio | cada 10 min | BD → `alerts` → portal, Windows, Telegram |
| `notes-index` | Indexa las notas Markdown y versiona el vault en Git | cada hora | `data\knowledge` → `notes` |
| `statements` | Importa extractos nuevos (PDF/xlsx) del correo y de `Documentos\PERSONAL_OS-Finanzas\Extractos`, actualiza saldos, tarjetas y tareas de pago; el día 6 envía el resumen del mes por Telegram | cada 6 h | correo + carpeta → `transactions`, `balances`, `card_statements` |
| `repos-scan` | Estado Git de `Documents\GitHub\*` | diario 12:00 | repos → `repos` |
| `briefing` | Briefing de la mañana (puede despertar el portátil suspendido) | diario 07:00 | BD → `data\briefings\*-morning.md` + Telegram |
| `evening-review` | Revisión de la noche | diario 21:00 | BD → `*-evening.md` |
| `weekly-review` | Revisión semanal | domingo 19:00 | BD → `*-weekly.md` |
| `cleanup` | Borra lo regenerable/vencido; reporta desorden, duplicados y repos huérfanos | domingo 20:30 | proyecto + BD → alerta si hay hallazgos |
| `backup` | Prepara la copia ESENCIAL (≤1 GB) en C: y una copia **cifrada en OneDrive** | domingo 21:00 | `config\backup.json` → repo `essential` + OneDrive |
| `restore-test` | Verifica integridad y restaura un archivo real | domingo 21:30 | repo esencial → resultado |
| `backup-essential` | **Manual**: doble clic `scripts\Backup-Esencial.cmd` (mensual) | plan + calendario, día 5 | repo local → disco externo |
| `backup-full` | **Manual**: doble clic `scripts\Backup-Completo.cmd` (trimestral o bajo demanda) | plan + calendario, día 12 | orígenes → repo `full` del disco |
| `portal` | Servidor del portal en http://127.0.0.1:8765 | al iniciar sesión (se reinicia solo) | — |

Tarea de Claude Code (no es un job del sistema): `personal-os-avance`, **cada 2 horas** (con la app de Claude abierta; con candado `data\avance.lock` para no solaparse), revisa el sistema, sincroniza el calendario y avanza el plan según `ROADMAP.md`.

| `panel` | cada 3 h y al encender el PC | Construye la copia cifrada del panel y la publica en GitHub Pages si cambió algo | `pos panel publish` |
