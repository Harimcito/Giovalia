# Actualiza la instalación por perfiles en este Windows, usando el código extraído del ZIP.
$ErrorActionPreference = 'Stop'
$origen = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$programas = (Resolve-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'Programs')).Path
$instalada = Join-Path $programas 'Giovalia-Servidor'
$base = Join-Path $env:LOCALAPPDATA 'Gioval'
$python = Join-Path $base 'servidor-venv\Scripts\python.exe'
$pythonw = Join-Path $base 'servidor-venv\Scripts\pythonw.exe'
if ((Resolve-Path -LiteralPath $instalada).Path -ne $instalada -or
    (Get-Item -LiteralPath $instalada).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint) -or
    $origen -eq $instalada) {
    throw 'Extrae el ZIP de actualización en una carpeta nueva antes de continuar.'
}
foreach ($componente in @($python, $pythonw, (Join-Path $base 'Datos\biblioteca\cotizador.db'),
    (Join-Path $base 'Acceso\usuarios.db'), (Join-Path $base 'servidor-principal.json'),
    (Join-Path $origen 'operacion_comercial_ui.py'), (Join-Path $origen 'servidor_principal.py'))) {
    if (-not (Test-Path -LiteralPath $componente -PathType Leaf)) { throw "Falta un componente: $componente" }
}
$config = Get-Content -LiteralPath (Join-Path $base 'servidor-principal.json') -Raw | ConvertFrom-Json
$puerto = if ($config.port) { [int]$config.port } else { 8501 }
$marca = [Guid]::NewGuid().ToString('N').Substring(0, 12)
$preparada = Join-Path $programas "Giovalia-Servidor.preparada-$marca"
$anterior = Join-Path $programas "Giovalia-Servidor.anterior-$marca"

function Iniciar-Giovalia([string]$Ruta) {
    $argumento = '"' + (Join-Path $Ruta 'servidor_principal.py') + '"'
    Start-Process -FilePath $pythonw -ArgumentList $argumento -WorkingDirectory $Ruta -WindowStyle Hidden | Out-Null
}

function Probar-Giovalia([switch]$VersionNueva) {
    for ($i = 0; $i -lt 20; $i++) {
        try {
            $respuesta = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$puerto/_stcore/health" -TimeoutSec 2
            if ($respuesta.StatusCode -eq 200 -and $respuesta.Content.Trim() -eq 'ok') {
                if (-not $VersionNueva) { return }
                $version = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$puerto/api/giovalia/version" -TimeoutSec 2
                if ($version.Content.Trim() -eq 'gestion-comercial-v1') { return }
            }
        } catch { Start-Sleep -Seconds 1 }
    }
    throw 'El servidor actualizado no respondió al control de salud.'
}

function Detener-Giovalia {
    $entradas = @((Join-Path $instalada 'servidor_principal.py'), (Join-Path $instalada 'servidor_web.py'), (Join-Path $instalada 'app.py'))
    $procesos = @(Get-CimInstance Win32_Process | Where-Object {
        $cmdProceso = $_.CommandLine
        $_.Name -match '^python(w)?\.exe$' -and $cmdProceso -and
        @($entradas | Where-Object { $cmdProceso.IndexOf($_, [StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count -gt 0
    })
    foreach ($procActualizacion in ($procesos | Sort-Object ProcessId)) {
        Stop-Process -Id $procActualizacion.ProcessId -ErrorAction SilentlyContinue
    }
    foreach ($procActualizacion in $procesos) {
        Wait-Process -Id $procActualizacion.ProcessId -Timeout 8 -ErrorAction SilentlyContinue
    }
    if (Get-NetTCPConnection -State Listen -LocalPort $puerto -ErrorAction SilentlyContinue) {
        throw "El puerto $puerto sigue ocupado. Se canceló el reemplazo del programa."
    }
}

New-Item -Path $preparada -ItemType Directory | Out-Null
Get-ChildItem -LiteralPath $origen -Force | Where-Object {
    $_.Name -notin @('.git', '.venv', 'tests', 'tmp', '__pycache__', '.env', 'biblioteca', 'Datos', 'Acceso')
} | Copy-Item -Destination $preparada -Recurse -Force
foreach ($configPrivada in @('.env', 'configuracion.py', '.streamlitsecrets.toml')) {
    $configAnterior = Join-Path $instalada $configPrivada
    if (Test-Path -LiteralPath $configAnterior -PathType Leaf) {
        $configNueva = Join-Path $preparada $configPrivada
        New-Item -ItemType Directory -Path (Split-Path -Parent $configNueva) -Force | Out-Null
        Copy-Item -LiteralPath $configAnterior -Destination $configNueva -Force
    }
}
& $python -m compileall -q $preparada
if ($LASTEXITCODE -ne 0) { throw 'La comprobación de sintaxis del paquete falló.' }
& $python (Join-Path $preparada 'respaldo_servidor.py') $base (Join-Path $base 'Backups\Actualizacion')
if ($LASTEXITCODE -ne 0) { throw 'No se pudo verificar el respaldo previo.' }

$movidaAnterior = $false
$puestaNueva = $false
try {
    Detener-Giovalia
    foreach ($rutaComprobada in @($instalada, $preparada)) {
        $real = (Resolve-Path -LiteralPath $rutaComprobada).Path
        if ($real -ne $rutaComprobada -or (Split-Path -Parent $real) -ne $programas) {
            throw 'El directorio del programa está fuera de Programs.'
        }
    }
    Move-Item -LiteralPath $instalada -Destination $anterior
    $movidaAnterior = $true
    Move-Item -LiteralPath $preparada -Destination $instalada
    $puestaNueva = $true
    Iniciar-Giovalia $instalada
    Probar-Giovalia -VersionNueva
    Write-Host "Giovalia actualizado. El servidor respondió en el puerto $puerto."
} catch {
    $fallo = $_
    if ($movidaAnterior) {
        if ($puestaNueva) {
            Detener-Giovalia
            $real = (Resolve-Path -LiteralPath $instalada).Path
            if ($real -ne $instalada -or (Split-Path -Parent $real) -ne $programas) {
                throw 'No se pudo comprobar la ruta para revertir.'
            }
            Move-Item -LiteralPath $instalada -Destination $preparada
        }
        $real = (Resolve-Path -LiteralPath $anterior).Path
        if ($real -ne $anterior -or (Split-Path -Parent $real) -ne $programas) {
            throw 'No se pudo comprobar la ruta del programa anterior.'
        }
        Move-Item -LiteralPath $anterior -Destination $instalada
        Iniciar-Giovalia $instalada
        Probar-Giovalia
    } elseif (-not (Get-NetTCPConnection -State Listen -LocalPort $puerto -ErrorAction SilentlyContinue)) {
        Iniciar-Giovalia $instalada
    }
    throw $fallo
}
