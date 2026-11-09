$ErrorActionPreference = 'Stop'
$origenFase = Join-Path $PSScriptRoot 'Giovalia-servidor'
$baseFase = Join-Path $env:LOCALAPPDATA 'Gioval'
$codigoFase = Join-Path $env:LOCALAPPDATA 'Programs\Giovalia-Servidor'
$pythonFase = Join-Path $baseFase 'servidor-venv\Scripts\python.exe'
$pythonwFase = Join-Path $baseFase 'servidor-venv\Scripts\pythonw.exe'
$fechaFase = Get-Date -Format 'yyyyMMdd-HHmmss'
$respaldoFase = Join-Path $baseFase "Backups\Activacion-$fechaFase"
New-Item -ItemType Directory -Path $respaldoFase -Force | Out-Null

# Respaldo íntegro antes de tocar el programa o detener el proceso activo.
& $pythonFase (Join-Path $origenFase 'respaldo_servidor.py') $baseFase $respaldoFase
if ($LASTEXITCODE -ne 0) { throw 'Falló el respaldo; se canceló la activación.' }

# Código separado de la instalación que se actualizaba automáticamente desde main.
New-Item -ItemType Directory -Path $codigoFase -Force | Out-Null
Get-ChildItem -LiteralPath $origenFase -Force | Where-Object {
    $_.Name -notin @('__pycache__','.git','.pytest_cache','tests')
} | Copy-Item -Destination $codigoFase -Recurse -Force

$accesoFase = Join-Path $baseFase 'Acceso'
New-Item -ItemType Directory -Path $accesoFase -Force | Out-Null
$identidadFase = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $accesoFase /inheritance:r /grant:r "${identidadFase}:(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'No pude proteger la carpeta de cuentas.' }
& $pythonFase (Join-Path $codigoFase 'preparar_perfiles.py') $baseFase
if ($LASTEXITCODE -ne 0) { throw 'No pude preparar los perfiles.' }

@{port=8501;bind='127.0.0.1';radar=$false;codigo=$codigoFase;datos=(Join-Path $baseFase 'Datos')} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $baseFase 'servidor-principal.json') -Encoding utf8

$shellFase = New-Object -ComObject WScript.Shell
$desktopFase = [Environment]::GetFolderPath('Desktop')
$startupFase = [Environment]::GetFolderPath('Startup')
$atajoAnterior = Join-Path $desktopFase 'Gioval.lnk'
if (Test-Path $atajoAnterior) { Copy-Item -LiteralPath $atajoAnterior -Destination $respaldoFase }
foreach ($atajoFase in @((Join-Path $desktopFase 'Gioval.lnk'),(Join-Path $desktopFase 'GiovalIA compartida.lnk'))) {
    $lnkFase = $shellFase.CreateShortcut($atajoFase)
    $lnkFase.TargetPath = $pythonwFase
    $lnkFase.Arguments = '"' + (Join-Path $codigoFase 'abrir_compartido.py') + '"'
    $lnkFase.WorkingDirectory = $codigoFase
    $lnkFase.Description = 'GiovalIA: biblioteca central y acceso por perfil'
    $lnkFase.Save()
}
$autoFase = $shellFase.CreateShortcut((Join-Path $startupFase 'GiovalIA servidor principal.lnk'))
$autoFase.TargetPath = $pythonwFase
$autoFase.Arguments = '"' + (Join-Path $codigoFase 'servidor_principal.py') + '"'
$autoFase.WorkingDirectory = $codigoFase
$autoFase.Save()

# Evitar iniciar dos instalaciones del cotizador sobre el mismo JSON/SQLite.
$servidoresFase = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'streamlit run' -and
    $_.CommandLine -like '*Programs\Giovalia\app.py*'
})
foreach ($servidorFase in $servidoresFase) {
    & taskkill.exe /PID $servidorFase.ProcessId /T /F | Out-Null
}
Start-Sleep -Seconds 2
$ocupadoFase = Get-NetTCPConnection -State Listen -LocalPort 8501 -ErrorAction SilentlyContinue
if ($ocupadoFase) { throw 'El puerto 8501 sigue ocupado. No arranqué un segundo servidor.' }

$procFase = Start-Process -FilePath $pythonwFase -ArgumentList ('"' + (Join-Path $codigoFase 'servidor_principal.py') + '"') -WorkingDirectory $codigoFase -WindowStyle Hidden -PassThru
@{supervisorPid=$procFase.Id;activado=(Get-Date).ToString('o');respaldo=$respaldoFase} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $baseFase 'activacion-servidor.json') -Encoding utf8
Write-Output "Servidor principal activado. Respaldo: $respaldoFase"

for ($intentoFase=0; $intentoFase -lt 15; $intentoFase++) {
    try {
        $respuestaFase = Invoke-WebRequest 'http://127.0.0.1:8501/_stcore/health' -UseBasicParsing -TimeoutSec 2
        if ($respuestaFase.StatusCode -eq 200) { Write-Output 'Salud del servidor: OK'; break }
    } catch { Start-Sleep -Seconds 1 }
}

