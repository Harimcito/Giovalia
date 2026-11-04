@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0abrir_gioval.ps1"
if errorlevel 1 pause
