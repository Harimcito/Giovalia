$ErrorActionPreference = "Continue"
$baseDir = Join-Path $env:LOCALAPPDATA "Gioval"
$taskName = "Gioval - servidor con actualizaciones automaticas"
New-Item -ItemType Directory -Force -Path $baseDir | Out-Null
$stopSignal = Join-Path $baseDir "detener-actualizador.flag"
Set-Content -LiteralPath $stopSignal -Value "stop" -Encoding ASCII
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
}
Write-Host "Se desactivó el inicio automático de Gioval."
Write-Host "El supervisor cerrará ordenadamente las aplicaciones en un máximo aproximado de 30 segundos."
Write-Host "Después puedes volver a usar iniciar_servidor_gioval.bat e iniciar_radar_gioval.bat en modo manual."
Read-Host "Presiona Enter para cerrar"
