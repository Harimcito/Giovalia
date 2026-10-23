@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar_actualizaciones_servidor_gioval.ps1"
if errorlevel 1 pause
