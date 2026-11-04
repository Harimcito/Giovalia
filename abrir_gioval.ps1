$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path $PSScriptRoot).Path
$baseDir = Join-Path $env:LOCALAPPDATA "Gioval"
$dataDir = Join-Path $baseDir "Datos"
$venvPython = Join-Path $baseDir "servidor-venv\Scripts\python.exe"
$requirements = Join-Path $repoRoot "requirements.txt"
$installScript = Join-Path $repoRoot "instalar_servidor_gioval.ps1"
$installStamp = Join-Path $baseDir "requirements.sha256"
$pidFile = Join-Path $baseDir "servidor-local.pid"
$portFile = Join-Path $baseDir "servidor-local.port"
$logDir = Join-Path $baseDir "logs"

New-Item -ItemType Directory -Force -Path $baseDir, $dataDir, $logDir | Out-Null

function Show-Error([string]$Message) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($Message, "Gioval", "OK", "Error") | Out-Null
}

function Get-RequirementsHash {
    return (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
}

function Test-ServerHealth([int]$Port) {
    try {
        $response = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/_stcore/health" -f $Port) -UseBasicParsing -TimeoutSec 3
        return ($response.StatusCode -eq 200 -and $response.Content -match "ok")
    } catch {
        return $false
    }
}

function Get-ExistingGiovalServer {
    if (-not (Test-Path $pidFile) -or -not (Test-Path $portFile)) { return $null }
    try {
        $processId = [int][System.IO.File]::ReadAllText($pidFile).Trim()
        $port = [int][System.IO.File]::ReadAllText($portFile).Trim()
        $processInfo = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $processId) -ErrorAction Stop
        if ($null -ne $processInfo -and
            $processInfo.CommandLine -like "*$repoRoot*app.py*" -and
            (Test-ServerHealth $port)) {
            return $port
        }
    } catch {
        # Stale or unreadable markers are replaced by a fresh local server below.
    }
    return $null
}

function Get-FreePort {
    foreach ($candidate in @(8501, 8503, 8504, 8505, 8506, 8507, 8508, 8509, 8510)) {
        $listener = $null
        try {
            $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $candidate)
            $listener.Start()
            return $candidate
        } catch {
            # Try the next local port.
        } finally {
            if ($null -ne $listener) { $listener.Stop() }
        }
    }
    throw "No encontré un puerto local libre entre 8501 y 8510. Cierra otra aplicación que use esos puertos y vuelve a abrir Giovalia."
}

function Ensure-DesktopShortcut {
    $desktop = [Environment]::GetFolderPath("Desktop")
    if ([string]::IsNullOrWhiteSpace($desktop)) { return }
    $shortcutPath = Join-Path $desktop "Gioval.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $shortcut.Arguments = ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f (Join-Path $repoRoot "abrir_gioval.ps1"))
    $shortcut.WorkingDirectory = $repoRoot
    $shortcut.Description = "Inicia Giovalia y abre el cotizador en el navegador."
    $shortcut.IconLocation = (Join-Path $env:SystemRoot "System32\shell32.dll") + ",23"
    $shortcut.Save()
}

try {
    if (-not (Test-Path $requirements) -or -not (Test-Path (Join-Path $repoRoot "app.py"))) {
        throw "No encuentro los archivos de Giovalia. Vuelve a descargar el repositorio completo."
    }

    $expectedHash = Get-RequirementsHash
    $installedHash = if (Test-Path $installStamp) { [System.IO.File]::ReadAllText($installStamp).Trim() } else { "" }
    $pythonHealthy = $false
    if (Test-Path $venvPython) {
        try {
            & $venvPython -c "import streamlit" *> $null
            $pythonHealthy = ($LASTEXITCODE -eq 0)
        } catch {
            $pythonHealthy = $false
        }
    }
    if (-not $pythonHealthy -or $installedHash -ne $expectedHash) {
        $installerLog = Join-Path $logDir "instalacion-servidor.log"
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installScript -NonInteractive *>&1 | Tee-Object -FilePath $installerLog
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
            throw "No se pudo instalar el servidor de Giovalia. Revisa este registro: $installerLog"
        }
    }

    $port = Get-ExistingGiovalServer
    if ($null -eq $port) {
        $port = Get-FreePort
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $stdout = Join-Path $logDir ("local-{0}.log" -f $stamp)
        $stderr = Join-Path $logDir ("local-error-{0}.log" -f $stamp)
        $oldDataDir = $env:GIOVAL_DATA_DIR
        $oldRequire = $env:GIOVAL_REQUIRE_ACCESS
        $oldPassword = $env:GIOVAL_ACCESS_PASSWORD
        $oldImport = $env:GIOVAL_ALLOW_INITIAL_IMPORT
        $oldUtf8 = $env:PYTHONUTF8
        try {
            $env:GIOVAL_DATA_DIR = $dataDir
            $env:GIOVAL_REQUIRE_ACCESS = "false"
            $env:GIOVAL_ACCESS_PASSWORD = ""
            $env:GIOVAL_ALLOW_INITIAL_IMPORT = "false"
            $env:PYTHONUTF8 = "1"
            $arguments = '-m streamlit run "{0}" --server.address 127.0.0.1 --server.port {1} --server.headless true --server.fileWatcherType none --global.developmentMode false' -f (Join-Path $repoRoot "app.py"), $port
            $serverProcess = Start-Process -FilePath $venvPython -ArgumentList $arguments -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        } finally {
            $env:GIOVAL_DATA_DIR = $oldDataDir
            $env:GIOVAL_REQUIRE_ACCESS = $oldRequire
            $env:GIOVAL_ACCESS_PASSWORD = $oldPassword
            $env:GIOVAL_ALLOW_INITIAL_IMPORT = $oldImport
            $env:PYTHONUTF8 = $oldUtf8
        }
        [System.IO.File]::WriteAllText($pidFile, [string]$serverProcess.Id, [System.Text.Encoding]::ASCII)
        [System.IO.File]::WriteAllText($portFile, [string]$port, [System.Text.Encoding]::ASCII)

        $deadline = (Get-Date).AddSeconds(180)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 2
            if ($serverProcess.HasExited) {
                $details = if (Test-Path $stderr) { (Get-Content $stderr -Tail 30) -join "`n" } else { "No hay detalle adicional." }
                throw "El servidor de Giovalia se detuvo al iniciar.`n`n$details`n`nRegistro: $stderr"
            }
            if (Test-ServerHealth $port) { break }
        }
        if (-not (Test-ServerHealth $port)) {
            throw "El servidor no respondió a tiempo. Revisa el registro: $stderr"
        }
    }

    Ensure-DesktopShortcut
    Start-Process ("http://127.0.0.1:{0}" -f $port)
} catch {
    Show-Error $_.Exception.Message
    exit 1
}
