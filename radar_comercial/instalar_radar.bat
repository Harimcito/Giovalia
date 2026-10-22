@echo off
setlocal
cd /d "%~dp0"
set "BASE=%LOCALAPPDATA%\RadarComercialGioval"
set "VENV=%BASE%\venv"
if not exist "%BASE%" mkdir "%BASE%"
where py >nul 2>nul
if errorlevel 1 (
  where python >nul 2>nul
  if errorlevel 1 (
    echo No encuentro Python. Instala Python 3.11 o 3.12 y vuelve a intentarlo.
    pause
    exit /b 1
  )
  if not exist "%VENV%\Scripts\python.exe" python -m venv "%VENV%"
) else (
  if not exist "%VENV%\Scripts\python.exe" py -3 -m venv "%VENV%"
)
if not exist "%VENV%\Scripts\python.exe" (
  echo No pude crear el entorno virtual en "%VENV%".
  pause
  exit /b 1
)
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto error
"%VENV%\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto error
echo.
echo Radar Comercial quedo instalado.
echo Ejecuta iniciar_radar.bat para abrir la aplicacion.
pause
exit /b 0
:error
echo Hubo un error instalando las dependencias. Revisa la conexion a internet e intenta de nuevo.
pause
exit /b 1
