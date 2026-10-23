<#
  Crea (idempotente) los accesos directos de PERSONAL_OS, sin repetir y sin carpetas en el Escritorio:
    Escritorio:  «PERSONAL_OS» (el portal como app, con su ícono) y «Captura rapida» (atajo Ctrl+Alt+N).
    Menú Inicio: «PERSONAL_OS» (para anclarlo a la barra de tareas) y la carpeta «PERSONAL_OS (herramientas)» con correo, multimedia, backups y restablecer PIN.
  Los editores (GIMP, Shotcut, Inkscape, Upscayl, DaVinci) ya tienen su propio acceso al instalarse: aquí no se duplican.
  Uso:  powershell -File automation\Register-Shortcuts.ps1
#>
$ErrorActionPreference = 'Stop'
$root     = Split-Path $PSScriptRoot -Parent
$desktop  = [Environment]::GetFolderPath('Desktop')
$programs = [Environment]::GetFolderPath('Programs')
$tools    = Join-Path $programs 'PERSONAL_OS (herramientas)'
New-Item -ItemType Directory -Force $tools | Out-Null
$sh   = New-Object -ComObject WScript.Shell
$pyw  = (Get-Command pythonw.exe).Source
$edge = @('C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', 'C:\Program Files\Microsoft\Edge\Application\msedge.exe') | Where-Object { Test-Path $_ } | Select-Object -First 1
$ico  = Join-Path $root 'portal\static\favicon.ico'

function New-Link($dir, $name, $target, $arguments = '', $hotkey = '', $icon = '', $workdir = '', $desc = '') {
  $l = $sh.CreateShortcut((Join-Path $dir "$name.lnk"))
  $l.TargetPath = $target; $l.Arguments = $arguments
  if ($hotkey)  { $l.Hotkey = $hotkey }
  if ($icon)    { $l.IconLocation = $icon }
  if ($workdir) { $l.WorkingDirectory = $workdir }
  if ($desc)    { $l.Description = $desc }
  $l.Save(); "acceso: $name"
}

# El portal se abre como una app propia: ventana sin barra de direcciones, perfil de Edge aparte (Windows no la agrupa con tus ventanas de Edge) y su ícono.
$portalArgs = "--app=http://127.0.0.1:8765 --user-data-dir=`"$(Join-Path $root 'data\portal-app')`" --no-first-run --no-default-browser-check --window-size=1360,900"
if ($edge) {
  foreach ($d in @($desktop, $programs)) { New-Link $d 'PERSONAL_OS' $edge $portalArgs '' $ico '' 'PERSONAL_OS: tu gestor personal' }
} else { New-Link $desktop 'PERSONAL_OS' 'http://127.0.0.1:8765' }
New-Link $desktop 'Captura rapida' $pyw "`"$(Join-Path $root 'scripts\Captura.pyw')`"" 'CTRL+ALT+N' "$pyw,0" (Join-Path $root 'scripts') 'Anota una idea en un segundo (Ctrl+Alt+N)'

New-Link $tools 'Correo (todas las cuentas)' "$env:LOCALAPPDATA\Programs\Thunderbird\thunderbird.exe" "-profile `"$(Join-Path $root 'data\mail-profile')`""
New-Link $tools 'Multimedia (arrastra archivos)' (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'PERSONAL_OS-Media')
New-Link $tools 'Restablecer PIN del portal' (Join-Path $root 'scripts\Restablecer-PIN.cmd') '' '' '' (Join-Path $root 'scripts')
New-Link $tools 'Backup esencial (disco externo)' (Join-Path $root 'scripts\Backup-Esencial.cmd') '' '' '' (Join-Path $root 'scripts')
New-Link $tools 'Backup completo (disco externo)' (Join-Path $root 'scripts\Backup-Completo.cmd') '' '' '' (Join-Path $root 'scripts')

# Limpieza de lo que dejaron versiones anteriores de este script (a la Papelera, no se borra de forma definitiva)
Add-Type -AssemblyName Microsoft.VisualBasic
$old = Join-Path $desktop 'PERSONAL_OS'
if (Test-Path $old -PathType Container) {
  [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($old, 'OnlyErrorDialogs', 'SendToRecycleBin'); "carpeta antigua del Escritorio enviada a la Papelera"
}
