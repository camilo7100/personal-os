<#
  Registra (idempotente) los jobs y el portal de PERSONAL_OS en el Programador de tareas de Windows, carpeta \PERSONAL_OS\.
  Se ejecutan con tu usuario, sin ventana, y se ponen al día si el PC estaba apagado (StartWhenAvailable).
  Fuente de verdad del catálogo: JOBS.md. Uso:  powershell -File automation\Register-Jobs.ps1
#>
$ErrorActionPreference = 'Stop'
$root   = Split-Path $PSScriptRoot -Parent
$pyw    = (Get-Command pythonw.exe).Source
$runner = Join-Path $root 'scripts\run_job.pyw'
$folder = '\PERSONAL_OS\'
$every  = { param($span) New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval $span }
$daily  = { param($at) New-ScheduledTaskTrigger -Daily -At $at }
$sunday = { param($at) New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At $at }

$jobs = @(
  @{ Name = 'health';           Trigger = (& $every (New-TimeSpan -Hours 1)) },
  @{ Name = 'mail-ingest';      Trigger = (& $every (New-TimeSpan -Minutes 30)) },
  @{ Name = 'telegram-capture'; Trigger = (& $every (New-TimeSpan -Minutes 2)) },
  @{ Name = 'media-watch';      Trigger = (& $every (New-TimeSpan -Minutes 10)) },
  @{ Name = 'inbox-triage';     Trigger = (& $every (New-TimeSpan -Hours 1)) },
  @{ Name = 'reminders';        Trigger = (& $every (New-TimeSpan -Minutes 10)) },
  @{ Name = 'notes-index';      Trigger = (& $every (New-TimeSpan -Hours 1)) },
  @{ Name = 'statements';       Trigger = (& $every (New-TimeSpan -Hours 6)) },
  @{ Name = 'panel';            Trigger = (& $every (New-TimeSpan -Hours 3)) },
  @{ Name = 'repos-scan';       Trigger = (& $daily '12:00') },
  @{ Name = 'briefing';         Trigger = (& $daily '07:00'); Wake = $true },
  @{ Name = 'evening-review';   Trigger = (& $daily '21:00'); Wake = $true },
  @{ Name = 'weekly-review';    Trigger = (& $sunday '19:00') },
  @{ Name = 'cleanup';          Trigger = (& $sunday '20:30') },
  @{ Name = 'backup';           Trigger = (& $sunday '21:00') },
  @{ Name = 'restore-test';     Trigger = (& $sunday '21:30') }
)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
# El briefing y la revisión de la noche despiertan el PC si está SUSPENDIDO (no si está apagado); StartWhenAvailable los ejecuta al volver
$wakeSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -WakeToRun `
            -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
foreach ($j in $jobs) {
  $action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$runner`" $($j.Name)" -WorkingDirectory (Join-Path $root 'scripts')
  Register-ScheduledTask -TaskName "$folder$($j.Name)" -Action $action -Trigger $j.Trigger -Settings $(if ($j.Wake) { $wakeSettings } else { $settings }) `
    -Description "PERSONAL_OS job: $($j.Name)" -Force | Out-Null
  "job registrado: $($j.Name)"
}

# Portal: se inicia al iniciar sesión y se reinicia solo si se cae
$portalAction = New-ScheduledTaskAction -Execute $pyw -Argument "`"$(Join-Path $root 'portal\serve.pyw')`"" -WorkingDirectory (Join-Path $root 'portal')
$portalSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
                  -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "${folder}portal" -Action $portalAction -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) `
  -Settings $portalSettings -Description 'PERSONAL_OS portal en http://127.0.0.1:8765' -Force | Out-Null
'portal registrado (arranca al iniciar sesión)'

# Jobs que ya no existen se eliminan (orden: nada huérfano)
$known = ($jobs | ForEach-Object { $_.Name }) + 'portal'
Get-ScheduledTask -TaskPath $folder -ErrorAction SilentlyContinue | Where-Object { $known -notcontains $_.TaskName } | ForEach-Object {
  Unregister-ScheduledTask -TaskName $_.TaskName -TaskPath $folder -Confirm:$false; "eliminado job huérfano: $($_.TaskName)" }
