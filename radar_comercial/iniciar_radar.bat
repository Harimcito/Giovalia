@echo off
setlocal
cd /d "%~dp0"
set "BASE=%LOCALAPPDATA%\RadarComercialGioval"
set "PYTHON=%BASE%\venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo Primero ejecuta instalar_radar.bat.
  pause
  exit /b 1
)
if not defined RADAR_DATA_DIR set "RADAR_DATA_DIR=%BASE%\Datos"
if not exist "%RADAR_DATA_DIR%" mkdir "%RADAR_DATA_DIR%"
echo Iniciando Radar Comercial en http://localhost:8502
echo Datos locales: %RADAR_DATA_DIR%
echo Para detenerlo, pulsa Ctrl+C.
"%PYTHON%" -m streamlit run "%~dp0app.py" --server.address 127.0.0.1 --server.port 8502 --server.headless true --global.developmentMode false --server.fileWatcherType none
