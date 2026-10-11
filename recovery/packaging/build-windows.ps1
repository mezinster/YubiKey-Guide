# Builds dist\ykrecover-windows-x64.zip: ykrecover.exe, ykrecover-gui.exe, README-windows.txt, LICENSE.
# Run from any folder on Windows with Python 3.11+ on PATH, for example from cmd:
#   powershell -NoProfile -ExecutionPolicy Bypass -File recovery\packaging\build-windows.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Invoke-Checked {
    param([scriptblock]$Command, [string]$What)
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed with exit code $LASTEXITCODE" }
}

# Always start from a fresh venv and empty dist\ so only the pinned versions are built and shipped.
if (Test-Path .venv) { Remove-Item -Recurse -Force .venv }
if (Test-Path dist) { Remove-Item -Recurse -Force dist }

Invoke-Checked { python -m venv .venv } "python -m venv"
Invoke-Checked { .\.venv\Scripts\python -m pip install --upgrade pip } "pip upgrade"
Invoke-Checked { .\.venv\Scripts\python -m pip install -r requirements-windows.txt } "pip install -r requirements-windows.txt"
Invoke-Checked { .\.venv\Scripts\python -m PyInstaller --noconfirm --clean --distpath dist --workpath build ykrecover.spec } "PyInstaller"

Copy-Item README-windows.txt dist\ -Force
Copy-Item ..\..\LICENSE dist\LICENSE -Force
Compress-Archive -Force -DestinationPath dist\ykrecover-windows-x64.zip -Path `
    dist\ykrecover.exe, dist\ykrecover-gui.exe, dist\README-windows.txt, dist\LICENSE
Write-Host "Built $PSScriptRoot\dist\ykrecover-windows-x64.zip"
