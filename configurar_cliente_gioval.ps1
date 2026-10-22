$ErrorActionPreference = "Stop"

Write-Host "Configurar conexión con el servidor central de Gioval" -ForegroundColor Cyan
Write-Host "En la oficina usa la URL HTTP privada del computador central (ej. http://192.168.1.20:8501)."
Write-Host "Desde fuera de la oficina usa la URL HTTPS del túnel (ej. https://cotizador.gioval.cl)."
$url = (Read-Host "Dirección del servidor central").Trim().TrimEnd("/")

try {
    $uri = [System.Uri]::new($url)
} catch {
    Write-Host "La dirección no es válida." -ForegroundColor Red
    Read-Host "Presiona Enter para cerrar"
    exit 1
}

if (-not $uri.IsAbsoluteUri -or -not $uri.Host) {
    Write-Host "Escribe una dirección completa, incluyendo http:// o https://." -ForegroundColor Red
    Read-Host "Presiona Enter para cerrar"
    exit 1
}

if ($uri.Scheme -eq "https") {
    # Acceso remoto cifrado.
} elseif ($uri.Scheme -eq "http") {
    $hostName = $uri.DnsSafeHost.ToLowerInvariant()
    $allowed = $hostName -eq "localhost" -or $hostName -eq "::1" -or $hostName -match "\.local$"
    if ($hostName -match "^127\.") { $allowed = $true }
    if ($hostName -match "^10\.") { $allowed = $true }
    if ($hostName -match "^192\.168\.") { $allowed = $true }
    if ($hostName -match "^172\.(1[6-9]|2[0-9]|3[01])\.") { $allowed = $true }
    if (-not $allowed) {
        Write-Host "Por seguridad, HTTP solo se admite en una red privada. Usa HTTPS para internet." -ForegroundColor Red
        Read-Host "Presiona Enter para cerrar"
        exit 1
    }
    Write-Host "Aviso: HTTP sin cifrado solo debe usarse dentro de tu red local de confianza." -ForegroundColor Yellow
} else {
    Write-Host "Solo se aceptan direcciones HTTP o HTTPS." -ForegroundColor Red
    Read-Host "Presiona Enter para cerrar"
    exit 1
}

$configDir = Join-Path $env:LOCALAPPDATA "Gioval"
$configPath = Join-Path $configDir "desktop.json"
New-Item -ItemType Directory -Force -Path $configDir | Out-Null
$json = @{ url = $url } | ConvertTo-Json
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($configPath, $json, $utf8NoBom)

Write-Host ""
Write-Host "Conexión de Gioval guardada." -ForegroundColor Green
Write-Host "Dirección: $url"
Write-Host "Abre Gioval desde el acceso directo del escritorio o el menú Inicio."
Read-Host "Presiona Enter para cerrar"
