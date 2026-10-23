# RECOVERY

Necesitas: el disco externo (o la copia esencial en C:), la contraseña de restic (Bitwarden), y restic (`winget install restic.restic`).

**Restaurar un archivo o carpeta**
```powershell
$env:RESTIC_PASSWORD = "<clave de Bitwarden>"
restic -r <DISCO>:\PERSONAL_OS_BACKUP\essential snapshots            # o ...\full
restic -r <DISCO>:\PERSONAL_OS_BACKUP\essential restore latest --target C:\Restauracion --include "/C/Users/<usuario>/Documents/GitHub"
```
Los snapshots anteriores al 2026-10-12 guardan los proyectos bajo la ruta antigua `/C/Users/<usuario>/Proyectos` (la carpeta `Proyectos` ya no existe): para restaurarlos usa `--include "/C/Users/<usuario>/Proyectos"`.
**PC nuevo o disco dañado:** 1) instalar Windows, Git, Anaconda, restic; 2) restaurar el repo `essential` (proyectos + `data\pos.db` + `config`); 3) `conda create -n pos python=3.13` e instalar `typer fastapi uvicorn jinja2 psutil keyring watchdog pytest`; 4) `powershell -File automation\Register-Jobs.ps1`; 5) restaurar `full` para documentos, imágenes y videos.
**Base de datos:** cada backup incluye una copia consistente en `data\db-snapshot\pos-snapshot.db` (la base viva se excluye porque cambia mientras se copia). Para recuperarla: restaurar esa carpeta y renombrar el archivo a `data\pos.db`.
**Base de datos dañada:** restaurar `data\pos.db` desde el repo esencial (snapshot más reciente).
**Comprobación periódica:** el job `restore-test` (semanal) demuestra que se puede restaurar.
