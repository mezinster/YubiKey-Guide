# Builds dist\ykrecover-windows-x64.zip: one folder, ykrecover-windows-x64\, holding ykrecover.exe,
# ykrecover-gui.exe, the _internal folder they share, README-windows.txt and LICENSE.
# Run from any folder on Windows, for example from cmd:
#   powershell -NoProfile -ExecutionPolicy Bypass -File recovery\packaging\build-windows.ps1
#
# Python for the build: YKRECOVER_PYTHON (path to python.exe) if set, else Python 3.13 or 3.12 through the
# py launcher, else `python` on PATH. It must be 3.12.4+ or 3.13: pyscard (ykman's smartcard library) only
# ships Windows wheels up to 3.13, and 3.12.4+ creates temporary folders that only the current user can read.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Invoke-Checked {
    param([scriptblock]$Command, [string]$What)
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed with exit code $LASTEXITCODE" }
}

function Find-Python {
    $ErrorActionPreference = "Continue"  # a missing interpreter must not abort the search
    $candidates = @()
    if ($env:YKRECOVER_PYTHON) { $candidates += , @($env:YKRECOVER_PYTHON) }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidates += , @("py", "-3.13")
        $candidates += , @("py", "-3.12")
    }
    $candidates += , @("python")
    foreach ($candidate in $candidates) {
        $exe = $candidate[0]
        $pyArgs = @($candidate | Select-Object -Skip 1)
        try {
            $version = & $exe @pyArgs -c "import sys; print('%d.%d.%d' % sys.version_info[:3])" 2>$null
        } catch {
            continue
        }
        if ($LASTEXITCODE -ne 0 -or -not $version) { continue }
        $v = [version]"$version".Trim()
        if ($v.Major -eq 3 -and ($v.Minor -eq 13 -or ($v.Minor -eq 12 -and $v.Build -ge 4))) {
            Write-Host "Building with Python $v ($($candidate -join ' '))"
            return , $candidate
        }
        Write-Host "Skipping Python $v ($($candidate -join ' ')): the build needs Python 3.12.4+ or 3.13"
    }
    throw ("No suitable Python found. Install Python 3.13 from https://www.python.org/downloads/windows/ " +
           "(keep the 'py launcher' option ticked), or set YKRECOVER_PYTHON to a python.exe of version 3.12.4+ or 3.13.")
}

$python = Find-Python
$pythonExe = $python[0]
$pythonArgs = @($python | Select-Object -Skip 1)

# Always start from a fresh venv and empty dist\ so only the pinned versions are built and shipped.
if (Test-Path .venv) { Remove-Item -Recurse -Force .venv }
if (Test-Path dist) { Remove-Item -Recurse -Force dist }

Invoke-Checked { & $pythonExe @pythonArgs -m venv .venv } "python -m venv"
Invoke-Checked { .\.venv\Scripts\python -m pip install --upgrade pip } "pip upgrade"
# --only-binary: never try to compile pyscard (that needs Visual C++); fail with a clear message instead.
Invoke-Checked { .\.venv\Scripts\python -m pip install --only-binary=pyscard -r requirements-windows.txt } `
    "pip install -r requirements-windows.txt"
Invoke-Checked { .\.venv\Scripts\python -m PyInstaller --noconfirm --clean --distpath dist --workpath build ykrecover.spec } "PyInstaller"

$app = "dist\ykrecover-windows-x64"
foreach ($file in "ykrecover.exe", "ykrecover-gui.exe", "_internal") {
    if (-not (Test-Path (Join-Path $app $file))) { throw "PyInstaller did not create $app\$file" }
}
Copy-Item README-windows.txt $app -Force
Copy-Item ..\..\LICENSE (Join-Path $app "LICENSE") -Force
# The folder itself goes into the zip, so extracting it gives one ykrecover-windows-x64 folder.
Compress-Archive -Force -DestinationPath dist\ykrecover-windows-x64.zip -Path $app
Write-Host "Built $PSScriptRoot\dist\ykrecover-windows-x64.zip"
