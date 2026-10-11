# Builds dist\ykrecover-windows-x64.zip: ykrecover.exe, ykrecover-gui.exe, README-windows.txt, LICENSE.
# Run from any folder on Windows with Python 3.11+ on PATH.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements-windows.txt
.\.venv\Scripts\python -m PyInstaller --noconfirm --clean --distpath dist --workpath build ykrecover.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Copy-Item README-windows.txt dist\ -Force
Copy-Item ..\..\LICENSE dist\LICENSE -Force
Compress-Archive -Force -DestinationPath dist\ykrecover-windows-x64.zip -Path `
    dist\ykrecover.exe, dist\ykrecover-gui.exe, dist\README-windows.txt, dist\LICENSE
Write-Host "Built $PSScriptRoot\dist\ykrecover-windows-x64.zip"
