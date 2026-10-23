$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path $PSScriptRoot).Path
$supervisor = Join-Path $repoRoot "supervisar_servidor_gioval.ps1"
$git = Get-Command git.exe -ErrorAction SilentlyContinue
$baseDir = Join-Path $env:LOCALAPPDATA "Gioval"
$venvPython = Join-Path $baseDir "servidor-venv\Scripts\python.exe"
$secretFile = Join-Path $baseDir "clave-acceso.dpapi"
$taskName = "Gioval - servidor con actualizaciones automaticas"

try {
    if (-not (Test-Path (Join-Path $repoRoot ".git"))) {
        throw "Esta carpeta no es un clon Git. Debes clonar https://github.com/Harimcito/Giovalia.git para activar actualizaciones automáticas."
    }
    if (-not $git) {
        throw "Git for Windows no está instalado o no aparece en PATH. Instálalo y vuelve a ejecutar este archivo."
    }
    if (-not (Test-Path $venvPython)) {
        throw "No encuentro el entorno del servidor. Ejecuta instalar_servidor_gioval.bat primero."
    }
    if (-not (Test-Path $secretFile)) {
        throw "No encuentro la clave de acceso del servidor. Ejecuta iniciar_servidor_gioval.bat una vez, crea la clave y luego detén esa ventana con Ctrl+C."
    }

    foreach ($port in @(8501, 8502)) {
        try {
            $response = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/_stcore/health" -f $port) -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200 -and $response.Content -match "ok") {
                throw "Detecté una aplicación Gioval activa en el puerto $port. Detén las ventanas actuales del cotizador y de Radar con Ctrl+C y vuelve a ejecutar este archivo para evitar dos servidores compitiendo por los mismos puertos."
            }
        } catch {
            if ($_.Exception.Message -like "Detecté una aplicación Gioval activa*") { throw }
            # Que no haya respuesta es normal antes de iniciar el servidor.
        }
    }

    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action = New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $supervisor)
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([System.TimeSpan]::Zero) -MultipleInstances IgnoreNew

    $stopSignal = Join-Path $baseDir "detener-actualizador.flag"
    if (Test-Path $stopSignal) { Remove-Item -LiteralPath $stopSignal -Force }
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Mantiene Gioval y Radar disponibles y actualiza el código desde la rama main de GitHub cada cinco minutos." -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName

    Write-Host ""
    Write-Host "Actualización automática configurada." -ForegroundColor Green
    Write-Host "El servidor inicia ahora y también al iniciar sesión en Windows." -ForegroundColor Green
    Write-Host "Revisa el registro en: $baseDir\logs\actualizador-servidor.log"
    Write-Host "Cotizador: http://localhost:8501"
    Write-Host "Radar Comercial: http://localhost:8502"
    Write-Host "GitHub se consulta cada 5 minutos. Los datos permanecen en $baseDir\Datos, fuera del repositorio."
    Write-Host ""
    Read-Host "Presiona Enter para cerrar"
} catch {
    Write-Host ""
    Write-Host ("No se pudo configurar: {0}" -f $_.Exception.Message) -ForegroundColor Red
    Read-Host "Presiona Enter para cerrar"
    exit 1
}
