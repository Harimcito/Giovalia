$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$appFile = Join-Path $repoRoot "radar_comercial\app.py"
$venvPython = Join-Path $env:LOCALAPPDATA "Gioval\servidor-venv\Scripts\python.exe"
$baseDir = Join-Path $env:LOCALAPPDATA "Gioval"
$dataDir = Join-Path $baseDir "Datos"
$secretFile = Join-Path $baseDir "clave-acceso.dpapi"

if (-not (Test-Path $appFile)) {
    Write-Host "No encuentro radar_comercial\app.py. Este script debe estar en la carpeta del código de Giovalia." -ForegroundColor Red
    Read-Host "Presiona Enter para cerrar"
    exit 1
}
if (-not (Test-Path $venvPython)) {
    Write-Host "Primero ejecuta instalar_servidor_gioval.bat." -ForegroundColor Yellow
    Read-Host "Presiona Enter para cerrar"
    exit 1
}
if (-not (Test-Path $secretFile)) {
    Write-Host "No encuentro la clave del servidor. Ejecuta iniciar_servidor_gioval.bat una vez para configurarla." -ForegroundColor Red
    Read-Host "Presiona Enter para cerrar"
    exit 1
}

try {
    $encrypted = [System.IO.File]::ReadAllText($secretFile).Trim()
    $securePassword = ConvertTo-SecureString $encrypted
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    try {
        $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
} catch {
    Write-Host "No pude descifrar la clave guardada. Ejecuta con el mismo usuario de Windows que configuró Gioval." -ForegroundColor Red
    Read-Host "Presiona Enter para cerrar"
    exit 1
}

New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
$env:GIOVAL_DATA_DIR = $dataDir
$env:GIOVAL_REQUIRE_ACCESS = "true"
$env:GIOVAL_ACCESS_PASSWORD = $plainPassword
$env:PYTHONUTF8 = "1"

Write-Host ""
Write-Host "Radar Comercial de Gioval iniciado en el puerto 8502." -ForegroundColor Green
Write-Host "En este computador: http://localhost:8502"
Write-Host "Desde otro equipo de la misma red: http://IP-DEL-COMPUTADOR:8502"
Write-Host "Mantén abierta esta ventana. Para detener el Radar, presiona Ctrl+C." -ForegroundColor Yellow
Write-Host "Catálogo e historial se leen desde: $dataDir\biblioteca"
Write-Host "Oportunidades y evaluaciones se guardan en: $dataDir\radar_comercial.db"
Write-Host ""

try {
    Set-Location $repoRoot
    & $venvPython -m streamlit run $appFile --server.address 0.0.0.0 --server.port 8502 --server.headless true --global.developmentMode false --server.fileWatcherType none
} finally {
    $env:GIOVAL_ACCESS_PASSWORD = $null
    $env:GIOVAL_DATA_DIR = $null
    $env:GIOVAL_REQUIRE_ACCESS = $null
    $env:PYTHONUTF8 = $null
    $plainPassword = $null
}
