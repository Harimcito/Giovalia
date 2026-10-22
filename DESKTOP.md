# Gioval como aplicación de escritorio (Windows)

## Qué hace

Gioval.exe abre el cotizador en una ventana de escritorio propia. No abre una pestaña en Chrome o Edge. El instalador crea accesos directos en el menú Inicio y en el escritorio.

La aplicación mantiene Streamlit como motor local, alojado únicamente en 127.0.0.1. No publica la aplicación en internet ni en la red local.

## Crear y descargar el instalador

El flujo automático de GitHub Actions compila para Windows x64 el programa y el instalador. Después de integrar los cambios en main:

1. Abre la pestaña **Actions** del repositorio.
2. Selecciona el flujo **Crear instalador de escritorio para Windows**.
3. En la última ejecución, descarga el artefacto **Gioval-Instalador-Windows-x64**.
4. Envía el archivo **Gioval-Setup.exe** a la persona que lo instalará.

El flujo vuelve a crear el artefacto cuando cambian los archivos principales del cotizador. Es un instalador por usuario y está diseñado para Windows x64.

## Datos del usuario

La base local y la biblioteca predeterminada se guardan fuera de la carpeta del programa, en:

    %LOCALAPPDATA%\Gioval\
        biblioteca\
        .streamlit\
        .env

Esto evita guardar datos en la carpeta de instalación y permite conservarlos al actualizar o desinstalar. El instalador no incluye una copia del catálogo ni del historial que tengas en tu computador; esos datos son locales. Para trasladarlos, usa **Ajustes → Copia de seguridad** y restaura o copia la biblioteca en el otro computador. Cada computador mantiene su propia base de datos e historial.

La clave de OpenAI no forma parte del instalador. Cada usuario puede configurarla en Ajustes. Las funciones de interpretación con IA y búsqueda de imágenes/fichas requieren conexión a internet.

## Requisitos

- Windows x64.
- El instalador intenta instalar Microsoft Edge WebView2 Runtime si no lo encuentra. En ese caso necesita conexión a internet para descargar el runtime; en Windows 11 suele venir incluido.
- No es necesario instalar Python ni ejecutar comandos en el computador de destino.

## Desarrollo local

Después de instalar las dependencias de requirements-desktop.txt, ejecuta desde la carpeta del proyecto:

    python desktop_launcher.py

Para generar el paquete en un computador Windows con Inno Setup instalado, ejecuta:

    python -m PyInstaller --noconfirm --clean gioval_desktop.spec
    Invoke-WebRequest -Uri "https://go.microsoft.com/fwlink/p/?LinkId=2124703" -OutFile "installer/webview2/MicrosoftEdgeWebview2Setup.exe"
    $compiler = [System.IO.Path]::Combine([Environment]::GetFolderPath("ProgramFilesX86"), "Inno Setup 6\ISCC.exe")
    & $compiler "installer/Gioval.iss"
