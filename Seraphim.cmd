@echo off
rem Double-click to launch Seraphim (backend + desktop app)
where pwsh >nul 2>nul
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1"
)
if errorlevel 1 pause
