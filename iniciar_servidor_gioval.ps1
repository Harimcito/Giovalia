param([switch]$PermitirImportacionInicial)

$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$appFile = Join-Path $repoRoot "app.py"
$venvPython = Join-Path $env:LOCALAPPDATA "Gioval\servidor-venv\Scripts\python.exe"
$baseDir = Join-Path $env:LOCALAPPDATA "Gioval"
$dataDir = Join-Path $baseDir "Datos"
$secretFile = Join-Path $baseDir "clave-acceso.dpapi"

if (-not (Test-Path $appFile)) {
    Write-Host "No encuentro app.py. Este script debe estar en la carpeta del código de Giovalia." -ForegroundColor Red
    Read-Host "Presiona Enter para cerrar"
    exit 1
}
if (-not (Test-Path $venvPython)) {
    Write-Host "Primero ejecuta instalar_servidor_gioval.bat." -ForegroundColor Yellow
    Read-Host "Presiona Enter para cerrar"
    exit 1
}

function Convert-SecureStringToPlainText([System.Security.SecureString]$SecureValue) {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
if (-not (Test-Path $secretFile)) {
    Write-Host "La primera vez debes crear la clave que usará el equipo para acceder a Gioval." -ForegroundColor Cyan
    do {
        $securePassword = Read-Host "Crea una clave compartida (mínimo 12 caracteres)" -AsSecureString
        $plainPassword = Convert-SecureStringToPlainText $securePassword
        if ($plainPassword.Length -lt 12) {
            Write-Host "La clave debe tener al menos 12 caracteres." -ForegroundColor Yellow
        }
    } while ($plainPassword.Length -lt 12)
    $encrypted = ConvertFrom-SecureString $securePassword
    [System.IO.File]::WriteAllText($secretFile, $encrypted, [System.Text.Encoding]::ASCII)
    $plainPassword = $null
} else {
    try {
        $encrypted = [System.IO.File]::ReadAllText($secretFile).Trim()
        $securePassword = ConvertTo-SecureString $encrypted
    } catch {
        Write-Host "No pude descifrar la clave guardada. Inicia sesión en Windows con el mismo usuario que configuró el servidor." -ForegroundColor Red
        Read-Host "Presiona Enter para cerrar"
        exit 1
    }
}

$env:GIOVAL_DATA_DIR = $dataDir
$env:GIOVAL_REQUIRE_ACCESS = "true"
$env:GIOVAL_ALLOW_INITIAL_IMPORT = if ($PermitirImportacionInicial) { "true" } else { "false" }
$env:GIOVAL_ACCESS_PASSWORD = Convert-SecureStringToPlainText $securePassword
$env:PYTHONUTF8 = "1"

Write-Host ""
Write-Host "Servidor central de Gioval iniciado." -ForegroundColor Green
Write-Host "En este computador: http://localhost:8501"
Write-Host "En la misma red: http://IP-DEL-COMPUTADOR:8501"
Write-Host "Mantén esta ventana abierta. Para detener el servidor, presiona Ctrl+C." -ForegroundColor Yellow
Write-Host "Los datos se guardan fuera del repositorio, en: $dataDir\biblioteca"
Write-Host ""

try {
    Set-Location $repoRoot
    & $venvPython -m streamlit run $appFile --server.address 0.0.0.0 --server.port 8501 --server.headless true --server.fileWatcherType none
} finally {
    $env:GIOVAL_ACCESS_PASSWORD = $null
    $env:GIOVAL_DATA_DIR = $null
    $env:GIOVAL_REQUIRE_ACCESS = $null
    $env:GIOVAL_ALLOW_INITIAL_IMPORT = $null
    $env:PYTHONUTF8 = $null
}
