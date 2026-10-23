<#
  Instala los editores gráficos que exigen permisos de administrador (no se pueden instalar en segundo plano).
  USO: clic derecho sobre este archivo → "Ejecutar con PowerShell" → acepta el aviso de permisos.
  Ya instalados sin administrador: Shotcut (video) y GIMP 3 (imágenes).
#>
#Requires -RunAsAdministrator
$ErrorActionPreference = 'Continue'
$apps = @(
  @{ Id = 'Inkscape.Inkscape'; Name = 'Inkscape (logos y vectores)' },
  @{ Id = 'Upscayl.Upscayl';   Name = 'Upscayl (mejorar imágenes con IA local)' }
)
foreach ($a in $apps) {
  Write-Host "Instalando $($a.Name)…" -ForegroundColor Cyan
  winget install --id $a.Id --source winget --silent --accept-package-agreements --accept-source-agreements
}
Write-Host "`nOpcional (video profesional y corrección de color): DaVinci Resolve, gratis." -ForegroundColor Yellow
Write-Host "Se descarga desde https://www.blackmagicdesign.com/products/davinciresolve (pide un formulario; no está en winget)."
Write-Host "`nListo. Para ver qué editores detecta PERSONAL_OS:  scripts\pos.cmd media editors"
