param()

$ErrorActionPreference = "Stop"
$script:repoRoot = (Resolve-Path $PSScriptRoot).Path
$script:baseDir = Join-Path $env:LOCALAPPDATA "Gioval"
$script:dataDir = Join-Path $script:baseDir "Datos"
$script:venvPython = Join-Path $script:baseDir "servidor-venv\Scripts\python.exe"
$script:secretFile = Join-Path $script:baseDir "clave-acceso.dpapi"
$script:logDir = Join-Path $script:baseDir "logs"
$script:supervisorLog = Join-Path $script:logDir "actualizador-servidor.log"
$script:gitExe = (Get-Command git.exe -ErrorAction SilentlyContinue).Source
$script:plainPassword = $null
$script:cotizadorProcess = $null
$script:radarProcess = $null
$script:cotizadorErrorLog = $null
$script:radarErrorLog = $null
$script:pollSeconds = 30
$script:updateIntervalSeconds = 300

New-Item -ItemType Directory -Force -Path $script:logDir, $script:dataDir | Out-Null

function Write-Log([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $script:supervisorLog -Value $line -Encoding UTF8
    Write-Output $line
}

function Invoke-GitText([string[]]$GitArgs) {
    $output = & $script:gitExe -C $script:repoRoot @GitArgs 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw ("git {0} falló: {1}" -f ($GitArgs -join " "), ($output -join " "))
    }
    return (($output | Out-String).Trim())
}

function Test-StreamlitHealth([int]$Port) {
    try {
        $response = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/_stcore/health" -f $Port) -UseBasicParsing -TimeoutSec 4
        return ($response.StatusCode -eq 200 -and $response.Content -match "ok")
    } catch {
        return $false
    }
}

function Stop-AppProcesses {
    foreach ($proc in @($script:cotizadorProcess, $script:radarProcess)) {
        if ($null -ne $proc) {
            try {
                if (-not $proc.HasExited) {
                    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
                    $null = $proc.WaitForExit(5000)
                }
            } catch {
                # El proceso puede haber terminado entre la comprobación y el cierre.
            }
        }
    }
    $script:cotizadorProcess = $null
    $script:radarProcess = $null
}

function Start-AppProcesses {
    if (-not (Test-Path $script:venvPython)) {
        throw "No existe el entorno Python: $script:venvPython. Ejecuta instalar_servidor_gioval.bat."
    }
    $cotizadorApp = Join-Path $script:repoRoot "app.py"
    $radarApp = Join-Path $script:repoRoot "radar_comercial\app.py"
    if (-not (Test-Path $cotizadorApp)) { throw "No encuentro app.py en $script:repoRoot." }
    if (-not (Test-Path $radarApp)) { throw "No encuentro radar_comercial\app.py en $script:repoRoot." }

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $cotizadorOut = Join-Path $script:logDir "cotizador-$stamp.log"
    $script:cotizadorErrorLog = Join-Path $script:logDir "cotizador-error-$stamp.log"
    $radarOut = Join-Path $script:logDir "radar-$stamp.log"
    $script:radarErrorLog = Join-Path $script:logDir "radar-error-$stamp.log"

    $oldData = $env:GIOVAL_DATA_DIR
    $oldRequire = $env:GIOVAL_REQUIRE_ACCESS
    $oldImport = $env:GIOVAL_ALLOW_INITIAL_IMPORT
    $oldPassword = $env:GIOVAL_ACCESS_PASSWORD
    $oldUtf8 = $env:PYTHONUTF8

    try {
        $env:GIOVAL_DATA_DIR = $script:dataDir
        $env:GIOVAL_REQUIRE_ACCESS = "true"
        $env:GIOVAL_ALLOW_INITIAL_IMPORT = "false"
        $env:GIOVAL_ACCESS_PASSWORD = $script:plainPassword
        $env:PYTHONUTF8 = "1"

        $cotizadorArgs = '-m streamlit run "{0}" --server.address 0.0.0.0 --server.port 8501 --server.headless true --server.fileWatcherType none' -f $cotizadorApp
        $script:cotizadorProcess = Start-Process -FilePath $script:venvPython -ArgumentList $cotizadorArgs -WorkingDirectory $script:repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $cotizadorOut -RedirectStandardError $script:cotizadorErrorLog

        $radarArgs = '-m streamlit run "{0}" --server.address 0.0.0.0 --server.port 8502 --server.headless true --global.developmentMode false --server.fileWatcherType none' -f $radarApp
        $script:radarProcess = Start-Process -FilePath $script:venvPython -ArgumentList $radarArgs -WorkingDirectory $script:repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $radarOut -RedirectStandardError $script:radarErrorLog
    } finally {
        $env:GIOVAL_DATA_DIR = $oldData
        $env:GIOVAL_REQUIRE_ACCESS = $oldRequire
        $env:GIOVAL_ALLOW_INITIAL_IMPORT = $oldImport
        $env:GIOVAL_ACCESS_PASSWORD = $oldPassword
        $env:PYTHONUTF8 = $oldUtf8
    }

    $deadline = (Get-Date).AddSeconds(180)
    do {
        Start-Sleep -Seconds 3
        if ($null -eq $script:cotizadorProcess -or $script:cotizadorProcess.HasExited) {
            throw "El cotizador no pudo iniciar. Revisa $script:cotizadorErrorLog."
        }
        if ($null -eq $script:radarProcess -or $script:radarProcess.HasExited) {
            throw "Radar Comercial no pudo iniciar. Revisa $script:radarErrorLog."
        }
        if ((Test-StreamlitHealth 8501) -and (Test-StreamlitHealth 8502)) {
            Write-Log "Cotizador (8501) y Radar Comercial (8502) están disponibles."
            return
        }
    } while ((Get-Date) -lt $deadline)

    throw "Las aplicaciones no respondieron dentro de 180 segundos. Revisa los archivos de log en $script:logDir."
}

function Update-RepositoryIfNeeded {
    if (-not $script:gitExe) {
        Write-Log "No se encontró Git. No se puede actualizar automáticamente; instala Git for Windows."
        return
    }

    $branch = Invoke-GitText -GitArgs @("branch", "--show-current")
    if ($branch -ne "main") {
        Write-Log "La carpeta está en la rama '$branch', no en 'main'. No se aplicó ninguna actualización."
        return
    }

    $status = & $script:gitExe -C $script:repoRoot status --porcelain --untracked-files=all 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Log ("No pude revisar los cambios locales: {0}" -f ($status -join " "))
        return
    }
    if (@($status | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }).Count -gt 0) {
        Write-Log "Hay cambios locales sin confirmar. Se omite la actualización para no sobrescribir trabajo local; guarda/retira esos cambios y vuelve a intentarlo."
        return
    }

    try {
        $null = Invoke-GitText -GitArgs @("fetch", "--quiet", "origin", "main")
        $current = Invoke-GitText -GitArgs @("rev-parse", "HEAD")
        $remote = Invoke-GitText -GitArgs @("rev-parse", "origin/main")
        $failedUpdateFile = Join-Path $script:baseDir "failed-update-commit.txt"
        if (Test-Path $failedUpdateFile) {
            $previouslyFailed = [System.IO.File]::ReadAllText($failedUpdateFile).Trim()
            if ($previouslyFailed -eq $remote) {
                Write-Log "La versión $remote ya falló al iniciar y se mantiene en cuarentena; no se intentará repetidamente hasta que main cambie."
                return
            }
        }
        if ($current -eq $remote) { return }

        & $script:gitExe -C $script:repoRoot merge-base --is-ancestor $current $remote *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Log "La rama main local no puede actualizarse con avance rápido desde origin/main. No se cambió el código."
            return
        }

        $oldHead = $current
        Write-Log "Nueva versión detectada en GitHub ($current -> $remote). Pausando las aplicaciones para actualizar."
        Stop-AppProcesses

        try {
            $null = Invoke-GitText -GitArgs @("pull", "--ff-only", "origin", "main")
            & $script:venvPython -m pip install -r (Join-Path $script:repoRoot "requirements.txt")
            if ($LASTEXITCODE -ne 0) { throw "Falló la instalación de requirements.txt." }

            Start-AppProcesses
            if (Test-Path $failedUpdateFile) { Remove-Item -LiteralPath $failedUpdateFile -Force -ErrorAction SilentlyContinue }
            $newHead = Invoke-GitText -GitArgs @("rev-parse", "HEAD")
            Write-Log "Actualización aplicada correctamente: $newHead"
        } catch {
            $updateError = $_.Exception.Message
            $headAfterFailure = Invoke-GitText -GitArgs @("rev-parse", "HEAD")
            $newCodeHadBeenApplied = ($headAfterFailure -ne $oldHead)
            Write-Log "La versión nueva no arrancó correctamente: $updateError. Intentando volver a $oldHead."
            Stop-AppProcesses
            & $script:gitExe -C $script:repoRoot reset --hard $oldHead 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Falló la actualización y también el rollback a $oldHead. Error original: $updateError"
            }
            & $script:venvPython -m pip install -r (Join-Path $script:repoRoot "requirements.txt")
            if ($LASTEXITCODE -ne 0) { throw "Se revirtió el código, pero falló reinstalar los requisitos de la versión anterior." }
            if ($newCodeHadBeenApplied) {
                [System.IO.File]::WriteAllText($failedUpdateFile, $remote, [System.Text.Encoding]::ASCII)
            }
            Start-AppProcesses
            if ($newCodeHadBeenApplied) {
                Write-Log "Rollback completado. Se mantiene activa la versión anterior ($oldHead). La revisión $remote queda en cuarentena para evitar reinicios repetidos."
            } else {
                Write-Log "No se llegó a aplicar el cambio de código. Se mantiene la versión anterior y se reintentará en la próxima comprobación."
            }
        }
    } catch {
        Write-Log ("No pude comprobar/aplicar actualizaciones: {0}" -f $_.Exception.Message)
    }
}

try {
    if (-not (Test-Path (Join-Path $script:repoRoot ".git"))) {
        throw "Esta carpeta no es un clon Git. Para actualizar desde GitHub automáticamente, clona https://github.com/Harimcito/Giovalia.git y ejecuta los scripts desde esa carpeta."
    }
    if (-not $script:gitExe) { throw "No se encontró Git for Windows. Instálalo y vuelve a iniciar el actualizador." }
    if (-not (Test-Path $script:secretFile)) {
        throw "No existe la clave de acceso guardada. Ejecuta iniciar_servidor_gioval.bat una vez y configura la clave antes de activar actualizaciones automáticas."
    }
    $encrypted = [System.IO.File]::ReadAllText($script:secretFile).Trim()
    $securePassword = ConvertTo-SecureString $encrypted
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    try {
        $script:plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }

    Write-Log "Supervisor iniciado para $script:repoRoot. Consultará GitHub cada 5 minutos."
    $stopSignal = Join-Path $script:baseDir "detener-actualizador.flag"
    try {
        Start-AppProcesses
    } catch {
        Stop-AppProcesses
        Write-Log ("El inicio no se completó; se reintentará en 30 segundos: {0}" -f $_.Exception.Message)
    }
    $nextUpdateCheck = (Get-Date).AddSeconds($script:updateIntervalSeconds)

    while ($true) {
        Start-Sleep -Seconds $script:pollSeconds

        if (Test-Path $stopSignal) {
            Remove-Item -LiteralPath $stopSignal -Force -ErrorAction SilentlyContinue
            Write-Log "Se recibió una solicitud de detención. Cerrando las aplicaciones de forma ordenada."
            break
        }

        $cotizadorDead = ($null -eq $script:cotizadorProcess -or $script:cotizadorProcess.HasExited)
        $radarDead = ($null -eq $script:radarProcess -or $script:radarProcess.HasExited)
        if ($cotizadorDead -or $radarDead) {
            Write-Log "Se detectó un proceso detenido. Reiniciando las aplicaciones."
            Stop-AppProcesses
            try {
                Start-AppProcesses
            } catch {
                Stop-AppProcesses
                Write-Log ("No pude reiniciar las aplicaciones; volveré a intentarlo: {0}" -f $_.Exception.Message)
            }
        }

        if ((Get-Date) -ge $nextUpdateCheck) {
            Update-RepositoryIfNeeded
            $nextUpdateCheck = (Get-Date).AddSeconds($script:updateIntervalSeconds)
        }
    }
} catch {
    Write-Log ("Error fatal del supervisor: {0}" -f $_.Exception.Message)
    exit 1
} finally {
    Stop-AppProcesses
    $env:GIOVAL_ACCESS_PASSWORD = $null
    $script:plainPassword = $null
}
