@echo off
setlocal

rem Double-click this file to start PSOAS.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Run-PSOAS.ps1" %*

if errorlevel 1 (
    echo.
    echo PSOAS could not start. Please keep this window open and contact support.
    pause
)

endlocal
