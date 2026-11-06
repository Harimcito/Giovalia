$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path $PSScriptRoot).Path
$baseDir = Join-Path $env:LOCALAPPDATA "Gioval"
$dataDir = Join-Path $baseDir "Datos"
$venvPython = Join-Path $baseDir "servidor-venv\Scripts\python.exe"
$requirements = Join-Path $repoRoot "requirements.txt"
$installScript = Join-Path $repoRoot "instalar_servidor_gioval.ps1"
$installStamp = Join-Path $baseDir "requirements.sha256"
$versionStamp = Join-Path $repoRoot "version-instalada.txt"
$updateStateDir = Join-Path $baseDir "actualizaciones"
$pidFile = Join-Path $baseDir "servidor-local.pid"
$portFile = Join-Path $baseDir "servidor-local.port"
$logDir = Join-Path $baseDir "logs"
$updateLog = Join-Path $logDir "actualizador-escritorio.log"

New-Item -ItemType Directory -Force -Path $baseDir, $dataDir, $logDir | Out-Null

function Write-UpdateLog([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $updateLog -Value $line -Encoding UTF8
}

function Get-GitHubMainCommit {
    $cacheFile = Join-Path $updateStateDir "main-commit.json"
    if (Test-Path $cacheFile) {
        try {
            $cache = Get-Content -LiteralPath $cacheFile -Raw | ConvertFrom-Json
            $checked = [DateTime]::Parse([string]$cache.checkedAt).ToUniversalTime()
            $cachedSha = [string]$cache.sha
            if ((([DateTime]::UtcNow - $checked).TotalMinutes -lt 5) -and $cachedSha -match '^[0-9a-f]{40}$') {
                return $cachedSha
            }
        } catch {
            # Ignore an invalid cache and ask GitHub.
        }
    }

    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $response = Invoke-RestMethod -Uri "https://api.github.com/repos/Harimcito/Giovalia/commits/main" -Headers @{
            Accept = "application/vnd.github+json"
            "User-Agent" = "Giovalia-Desktop-Updater"
        } -TimeoutSec 8
        $sha = [string]$response.sha
        if ($sha -notmatch '^[0-9a-f]{40}$') { throw "GitHub devolvió una versión con formato inesperado." }
        New-Item -ItemType Directory -Force -Path $updateStateDir | Out-Null
        @{ sha = $sha; checkedAt = [DateTime]::UtcNow.ToString("o") } |
            ConvertTo-Json | Set-Content -LiteralPath $cacheFile -Encoding UTF8
        return $sha
    } catch {
        Write-UpdateLog ("No pude consultar GitHub; iniciaré la versión ya instalada. Detalle: {0}" -f $_.Exception.Message)
        return $null
    }
}

function Test-ManagedServerForUpdate {
    if (-not (Test-Path $pidFile)) { return $false }
    $managedPid = 0
    try {
        $managedPid = [int][System.IO.File]::ReadAllText($pidFile).Trim()
    } catch {
        return $false
    }
    $processInfo = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $managedPid) -ErrorAction SilentlyContinue
    return ($null -ne $processInfo -and
        $processInfo.CommandLine -like "*$repoRoot*app.py*" -and
        $processInfo.CommandLine -match "streamlit")
}

function Update-DesktopFromGitHub {
    # A Git clone may be a development checkout. Its separate supervisor already
    # updates clean main checkouts, so never overlay one from the desktop launcher.
    if (Test-Path (Join-Path $repoRoot ".git")) { return }

    $latest = Get-GitHubMainCommit
    if (-not $latest) { return }
    $installed = if (Test-Path $versionStamp) { [System.IO.File]::ReadAllText($versionStamp).Trim() } else { "" }
    if ($installed -eq $latest) { return }
    if (Test-ManagedServerForUpdate) {
        Write-UpdateLog ("Encontré una versión nueva ({0}), pero Giovalia está abierta. La aplicaré cuando se inicie después de cerrar el servidor." -f $latest)
        return
    }

    $workDir = Join-Path $updateStateDir ("descarga-{0}-{1}" -f $latest.Substring(0, 8), ([Guid]::NewGuid().ToString("N")))
    $archiveFile = Join-Path $workDir "giovalia.zip"
    $stageDir = Join-Path $workDir "extraido"
    $backupDir = Join-Path $workDir "respaldo"
    $changedFiles = New-Object System.Collections.Generic.List[string]
    $backedUpFiles = @{}
    $versionBackup = Join-Path $workDir "version-instalada.txt"
    $versionExisted = Test-Path $versionStamp
    $copyStarted = $false

    try {
        New-Item -ItemType Directory -Force -Path $workDir, $stageDir, $backupDir | Out-Null
        Invoke-WebRequest -Uri ("https://codeload.github.com/Harimcito/Giovalia/zip/{0}" -f $latest) -OutFile $archiveFile -UseBasicParsing -TimeoutSec 45

        Add-Type -AssemblyName System.IO.Compression
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zip = [System.IO.Compression.ZipFile]::OpenRead($archiveFile)
        try {
            foreach ($entry in $zip.Entries) {
                $parts = $entry.FullName.Replace('\', '/').Split('/')
                if ($parts.Count -lt 2 -or $parts[0] -notmatch '^Giovalia-[0-9a-f]{40}$') { continue }
                $relative = ($parts | Select-Object -Skip 1) -join '/'
                if ([string]::IsNullOrWhiteSpace($relative) -or $entry.FullName.EndsWith('/')) { continue }
                if ($relative -match '(^|/)\.\.?(/|$)|:' -or
                    ($relative -match '(^|/)\.env($|\.)' -and $relative -notmatch '(^|/)\.env\.example$') -or
                    $relative -match '(^|/)(cotizador\.db|radar_comercial\.db|version-instalada\.txt)$' -or
                    $relative -match '(^|/)(biblioteca|Datos|logs|servidor-venv)(/|$)') { continue }

                $destination = [System.IO.Path]::GetFullPath((Join-Path $stageDir ($relative.Replace('/', '\'))))
                $stagePrefix = [System.IO.Path]::GetFullPath($stageDir) + [System.IO.Path]::DirectorySeparatorChar
                if (-not $destination.StartsWith($stagePrefix, [StringComparison]::OrdinalIgnoreCase)) {
                    throw "El archivo recibido desde GitHub apunta fuera de la carpeta de actualización."
                }
                New-Item -ItemType Directory -Force -Path ([System.IO.Path]::GetDirectoryName($destination)) | Out-Null
                $inputStream = $entry.Open()
                $outputStream = [System.IO.File]::Create($destination)
                try { $inputStream.CopyTo($outputStream) } finally { $outputStream.Dispose(); $inputStream.Dispose() }
            }
        } finally { $zip.Dispose() }

        if (-not (Test-Path (Join-Path $stageDir "app.py")) -or -not (Test-Path (Join-Path $stageDir "requirements.txt"))) {
            throw "La descarga de GitHub no contiene los archivos principales de Giovalia."
        }

        $files = @(Get-ChildItem -LiteralPath $stageDir -File -Recurse)
        if ($versionExisted) { Copy-Item -LiteralPath $versionStamp -Destination $versionBackup -Force }
        $copyStarted = $true
        foreach ($file in $files) {
            $relative = $file.FullName.Substring($stageDir.TrimEnd('\').Length + 1)
            $destination = Join-Path $repoRoot $relative
            $backup = Join-Path $backupDir $relative
            if (Test-Path $destination) {
                New-Item -ItemType Directory -Force -Path ([System.IO.Path]::GetDirectoryName($backup)) | Out-Null
                Copy-Item -LiteralPath $destination -Destination $backup -Force
                $backedUpFiles[$relative] = $true
            }
            New-Item -ItemType Directory -Force -Path ([System.IO.Path]::GetDirectoryName($destination)) | Out-Null
            $changedFiles.Add($relative)
            Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
        }
        [System.IO.File]::WriteAllText($versionStamp, $latest, [System.Text.Encoding]::ASCII)
        Write-UpdateLog ("Actualicé Giovalia desde GitHub: {0} -> {1}; archivos actualizados: {2}." -f $installed, $latest, $files.Count)
    } catch {
        if ($copyStarted) {
            foreach ($relative in $changedFiles) {
                $destination = Join-Path $repoRoot $relative
                if ($backedUpFiles.ContainsKey($relative)) {
                    Copy-Item -LiteralPath (Join-Path $backupDir $relative) -Destination $destination -Force -ErrorAction SilentlyContinue
                } else {
                    Remove-Item -LiteralPath $destination -Force -ErrorAction SilentlyContinue
                }
            }
            if ($versionExisted -and (Test-Path $versionBackup)) {
                Copy-Item -LiteralPath $versionBackup -Destination $versionStamp -Force -ErrorAction SilentlyContinue
            } elseif (-not $versionExisted) {
                Remove-Item -LiteralPath $versionStamp -Force -ErrorAction SilentlyContinue
            }
        }
        Write-UpdateLog ("No pude aplicar la versión {0}; conservaré la instalada. Detalle: {1}" -f $latest, $_.Exception.Message)
    } finally {
        Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

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

    Update-DesktopFromGitHub

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

