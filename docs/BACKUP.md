# BACKUP

Herramienta: restic (cifrado AES-256, deduplicado). Contraseña única en el Administrador de credenciales de Windows (servicio `pos`, usuario `restic-password`). **Guárdala también en Bitwarden** (`scripts\pos.cmd backup key`): sin ella no hay restauración.

| Escenario | Qué incluye | Peso | Dónde | Cuándo |
|---|---|---|---|---|
| **Esencial** | Datos del sistema (`data`, `config`), `Documents\GitHub` (todos los proyectos de código), `Documents\Maestria`; archivos > 25 MB excluidos. Tope 1 GB (la salud avisa si se supera) | ~0,1–0,4 GB | Preparada en C: (`PERSONAL_OS_BACKUP\essential`) cada domingo; **doble clic `scripts\Backup-Esencial.cmd`** la traslada al disco externo y recorta lo local (quedan 2 snapshots) | Preparada: semanal automática. Al disco: mensual (día 5, plan + calendario) |
| **Completo** | Documents (incluye `Documents\GitHub`), Pictures, Videos, Music, Desktop, Downloads, datos del sistema | todo el PC (cientos de GB) | Directo al disco externo, repo `full` (**doble clic `scripts\Backup-Completo.cmd`**); incremental; no se prepara en C: para no llenarlo | Trimestral (día 12) o cuando lo pidas |

Disco externo: se reconoce por el archivo `.pos-backup-disk` en su raíz (la primera vez el doble clic pregunta cuál es y lo marca; no depende de la letra). Cada copia termina con verificación de integridad y restauración real de un archivo; al cerrar bien, la tarea del plan se completa y se agenda la siguiente.

**Copia cifrada en OneDrive (automática):** cada domingo, tras preparar la copia esencial, se copia también a `OneDrive\PERSONAL_OS_BACKUP\essential` (restic cifra antes; OneDrive solo ve datos ilegibles). Es la protección si el PC y el disco externo fallan a la vez. Se desactiva vaciando `essential.offsite_repo` en `config\backup.json`.

Editar qué se copia: `config\backup.json` (`essential.sources`, `external.full_sources`, `excludes`).

Regla: la copia preparada en C: no protege de un fallo del disco; la protección real es la del disco externo. La salud avisa si pasan 35 días sin copia esencial o 120 sin completa.
