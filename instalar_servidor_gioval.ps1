$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$requirements = Join-Path $repoRoot "requirements.txt"
$appFile = Join-Path $repoRoot "app.py"
if (-not (Test-Path $requirements) -or -not (Test-Path $appFile)) {
    Write-Host "No encuentro app.py y requirements.txt. Ejecuta este archivo desde la carpeta de Giovalia." -ForegroundColor Red
    Read-Host "Presiona Enter para cerrar"
    exit 1
}

$baseDir = Join-Path $env:LOCALAPPDATA "Gioval"
$venvDir = Join-Path $baseDir "servidor-venv"
$pythonExe = Join-Path $venvDir "Scripts\python.exe"
New-Item -ItemType Directory -Force -Path $baseDir | Out-Null

if (-not (Test-Path $pythonExe)) {
    $pyLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        & $pyLauncher.Source -3.12 -m venv $venvDir
        if ($LASTEXITCODE -ne 0) {
            if (Test-Path $venvDir) { Remove-Item -Recurse -Force $venvDir }
            & $pyLauncher.Source -3 -m venv $venvDir
        }
    } else {
        $python = Get-Command "python.exe" -ErrorAction SilentlyContinue
        if (-not $python) {
            Write-Host "No encuentro Python. Instala Python 3.12 desde python.org y marca 'Add python.exe to PATH'." -ForegroundColor Red
            Read-Host "Presiona Enter para cerrar"
            exit 1
        }
        & $python.Source -m venv $venvDir
    }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $pythonExe)) {
        throw "No pude crear el entorno Python de Giovalia."
    }
}

Write-Host "Instalando/verificando dependencias. Esto puede tardar unos minutos..." -ForegroundColor Cyan
& $pythonExe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "No se pudo actualizar pip." }
& $pythonExe -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) { throw "No se pudieron instalar las dependencias de Gioval." }

$dataDir = Join-Path $baseDir "Datos"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

Write-Host ""
Write-Host "Instalación del servidor completada." -ForegroundColor Green
Write-Host "Datos y biblioteca central: $dataDir\biblioteca"
Write-Host "Siguiente paso: ejecuta iniciar_servidor_gioval.bat."
Read-Host "Presiona Enter para cerrar"
