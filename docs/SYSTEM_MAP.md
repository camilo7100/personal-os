# SYSTEM_MAP

```
USUARIO ── portal · Ctrl+Alt+N · Telegram (texto/voz) · terminal · doble clic (backups, media) ──┐
   │                                                                                            │
   ▼                                                                                            │
CLAUDE CODE (N4) ◄── Skills (.claude/skills) ── permisos (.claude/settings.json)                │
   │ Bash                MCP: Gmail · Calendar · Drive                                            │
   ▼                                                                                            │
CLI scripts\pos.cmd ── services ── (única puerta de escritura, audita todo) ◄── PORTAL 127.0.0.1:8765
   │
   ├─► AUTOMATIZACIÓN (N2)  Programador de Windows → run_job.pyw → job_runs
   │      mail-ingest ◄─ Thunderbird pos-mail (Gmail·Hotmail)     briefing · evening · weekly
   │      telegram-capture ─► inbox ─► inbox-triage (determinista) health · cleanup · repos-scan
   │      media-watch ─► FFmpeg · Pillow · faster-whisper · visión local   notes-index (Git)
   │      backup · restore-test  ──►  backup-essential / backup-full (doble clic → disco externo)
   ▼
DATOS
   ├─ data\pos.db (tareas, proyectos, objetivos, hábitos, inbox, emails, finanzas, empleo, personas,
   │               documentos, suscripciones, repos, notas(índice), job_runs, alerts, health, audit_log)
   ├─ data\knowledge\ (Markdown + Git) ◄── Obsidian (visor opcional)  ─► Anki (.apkg)
   └─ Calendario / Correo (externos = fuente)
   ▼
NOTIFICACIONES  toast de Windows · Telegram (celular)
```
