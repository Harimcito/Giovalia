param([switch]$NonInteractive)

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

$entornoValido = $false
if (Test-Path $pythonExe) {
    try {
        & $pythonExe -c "import sys, pip" *> $null
        $entornoValido = ($LASTEXITCODE -eq 0)
    } catch {
        $entornoValido = $false
    }
}

if (-not $entornoValido) {
    if (Test-Path $venvDir) {
        $anterior = "{0}.anterior-{1}" -f $venvDir, (Get-Date -Format "yyyyMMdd-HHmmss")
        Move-Item -LiteralPath $venvDir -Destination $anterior
        Write-Host "Conservé el entorno anterior en: $anterior" -ForegroundColor Yellow
    }
    $creado = $false
    $pyLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        foreach ($version in @("-3.14", "-3")) {
            & $pyLauncher.Source $version -m venv $venvDir
            if ($LASTEXITCODE -eq 0 -and (Test-Path $pythonExe)) {
                $creado = $true
                break
            }
            if (Test-Path $venvDir) { Remove-Item -LiteralPath $venvDir -Recurse -Force }
        }
    } else {
        $python = Get-Command "python.exe" -ErrorAction SilentlyContinue
        if ($python) {
            & $python.Source -m venv $venvDir
            $creado = ($LASTEXITCODE -eq 0 -and (Test-Path $pythonExe))
        }
    }
    if (-not $creado) {
        throw "No pude crear un entorno Python funcional para Giovalia."
    }
}

Write-Host "Instalando/verificando dependencias. Esto puede tardar unos minutos..." -ForegroundColor Cyan
& $pythonExe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "No se pudo actualizar pip." }
& $pythonExe -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) { throw "No se pudieron instalar las dependencias de Gioval." }
$requirementsHash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
[System.IO.File]::WriteAllText((Join-Path $baseDir "requirements.sha256"), $requirementsHash, [System.Text.Encoding]::ASCII)

$dataDir = Join-Path $baseDir "Datos"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

Write-Host ""
Write-Host "Instalación del servidor completada." -ForegroundColor Green
Write-Host "Datos y biblioteca central: $dataDir\biblioteca"
Write-Host "Siguiente paso: ejecuta iniciar_servidor_gioval.bat."
if (-not $NonInteractive) { Read-Host "Presiona Enter para cerrar" }
