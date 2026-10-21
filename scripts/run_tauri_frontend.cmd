@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_tauri_frontend.ps1" %*
exit /b %ERRORLEVEL%
