@echo off
rem Prepares this Windows machine to build and run the YubiKey recovery tool (see prepare-windows.ps1):
rem Chocolatey, Python 3.13, GnuPG, PATH and YKRECOVER_PYTHON. Run it normally (not "as administrator"):
rem it asks for administrator rights only for the installation step.
rem   Prepare-Windows.bat            prepare only
rem   Prepare-Windows.bat -Build     prepare, then build dist\ykrecover-windows-x64.zip as you
rem   Prepare-Windows.bat -WhatIf    show what would change
setlocal
set "SCRIPT_DIR=%~dp0"

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
