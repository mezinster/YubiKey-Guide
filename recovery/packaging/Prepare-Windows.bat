@echo off
rem Prepares this Windows machine to build and run the YubiKey recovery tool (see prepare-windows.ps1):
rem Chocolatey, Python 3.13, GnuPG, PATH and YKRECOVER_PYTHON. Asks for administrator rights.
rem   Prepare-Windows.bat            prepare only
rem   Prepare-Windows.bat -Build     prepare, then build dist\ykrecover-windows-x64.zip
rem   Prepare-Windows.bat -WhatIf    show what would change
setlocal
set "SCRIPT_DIR=%~dp0"

net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator rights...
    if "%~1"=="" (
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    ) else (
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
    )
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%prepare-windows.ps1" %*
set "CODE=%ERRORLEVEL%"
echo.
if not "%CODE%"=="0" (
    echo Preparation failed with exit code %CODE%. The log is in "%SCRIPT_DIR%logs".
) else (
    echo Preparation finished.
)
pause
exit /b %CODE%
