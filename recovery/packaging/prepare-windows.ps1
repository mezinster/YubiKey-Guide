<#
.SYNOPSIS
Prepares a Windows machine to build and run the YubiKey recovery tool.

.DESCRIPTION
Installs Chocolatey if missing, then Python 3.13 and GnuPG through Chocolatey (pinned versions; skipped when
already present), makes sure their folders are on the machine PATH, and sets YKRECOVER_PYTHON so
build-windows.ps1 always builds with Python 3.13. With -Build it then builds the executables.

Run Prepare-Windows.bat (double-click; it asks for administrator rights), or from an elevated prompt:
  powershell -NoProfile -ExecutionPolicy Bypass -File prepare-windows.ps1 [-Build] [-WhatIf]

-WhatIf only reports what would change and does not need administrator rights.
A log is written to logs\prepare_<date>.log next to this script.
#>
[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$WhatIf,
    [string]$LogPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Pinned Chocolatey packages (newest releases at least 14 days old on 2026-10-11).
$PythonPackage = "python313"
$PythonVersion = "3.13.15"
$PythonDir = "C:\Python313"
$GnupgPackage = "gnupg"
$GnupgVersion = "2.5.21"
$ChocoTimeoutSeconds = 900   # a hanging installer fails after 15 minutes instead of blocking forever

if (-not $LogPath) {
    $LogPath = Join-Path $PSScriptRoot ("logs\prepare_{0:yyyy-MM-dd_HH-mm-ss}.log" -f (Get-Date))
}
New-Item -ItemType Directory -Force -Path (Split-Path $LogPath) | Out-Null

function Write-Log {
    param([string]$Message, [ValidateSet("INFO", "WARN", "ERROR", "SUCCESS")][string]$Level = "INFO")
    $line = "[{0:HH:mm:ss}] [{1}] {2}" -f (Get-Date), $Level, $Message
    Add-Content -Path $LogPath -Value $line -Encoding UTF8
    $colors = @{ INFO = "Gray"; WARN = "Yellow"; ERROR = "Red"; SUCCESS = "Green" }
    Write-Host $line -ForegroundColor $colors[$Level]
}

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $identity).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Update-SessionPath {
    # Machine PATH first, then User PATH: the same order a new shell gets.
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

function Invoke-Native {
    # Runs a native command, copies its output to the console and the log, and returns its exit code.
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments | ForEach-Object { Write-Host "  $_"; Add-Content -Path $LogPath -Value "  $_" -Encoding UTF8 }
    return $LASTEXITCODE
}

function Install-Chocolatey {
    if (Get-Command choco -ErrorAction SilentlyContinue) {
        $version = (& choco --version | Select-Object -First 1)
        Write-Log "Chocolatey $version is installed"
        if ([version]($version -replace '[^0-9.].*$', '') -lt [version]"2.0.0") {
            if ($WhatIf) { Write-Log "Would upgrade Chocolatey to 2.x"; return }
            Write-Log "Upgrading Chocolatey to 2.x"
            if ((Invoke-Native choco @("upgrade", "chocolatey", "-y", "--no-progress")) -ne 0) {
                throw "Chocolatey upgrade failed (see $LogPath)"
            }
        }
        Repair-ChocolateySource
        return
    }
    if ($WhatIf) { Write-Log "Would install Chocolatey from https://community.chocolatey.org"; return }
    Write-Log "Installing Chocolatey (official installer from https://community.chocolatey.org/install.ps1)"
    Set-ExecutionPolicy Bypass -Scope Process -Force
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
    $installer = (New-Object Net.WebClient).DownloadString("https://community.chocolatey.org/install.ps1")
    # Chocolatey's installer is not written for strict mode: run it in its own scope with strict mode off.
    & { Set-StrictMode -Off; Invoke-Expression $installer }
    Update-SessionPath
    if (-not $env:ChocolateyInstall) {
        $env:ChocolateyInstall = [Environment]::GetEnvironmentVariable("ChocolateyInstall", "Machine")
    }
    if (-not (Get-Command choco -ErrorAction SilentlyContinue) -and $env:ChocolateyInstall) {
        $env:Path += ";$env:ChocolateyInstall\bin"
    }
    if (-not (Get-Command choco -ErrorAction SilentlyContinue)) { throw "Chocolatey installation failed (see $LogPath)" }
    Write-Log "Chocolatey $(& choco --version | Select-Object -First 1) installed" "SUCCESS"
}

function Repair-ChocolateySource {
    # Old installs point at chocolatey.org/api/v2, which no longer serves packages.
    foreach ($line in @(& choco source list --limit-output)) {
        $fields = "$line" -split '\|'
        if ($fields.Count -ge 2 -and $fields[1] -match '^https?://chocolatey\.org/api/v2') {
            if ($WhatIf) { Write-Log "Would point Chocolatey source '$($fields[0])' at community.chocolatey.org"; continue }
            Write-Log "Pointing Chocolatey source '$($fields[0])' at community.chocolatey.org" "WARN"
            [void](Invoke-Native choco @("source", "remove", "-n=$($fields[0])"))
            [void](Invoke-Native choco @("source", "add", "-n=$($fields[0])", "-s=https://community.chocolatey.org/api/v2/"))
        }
    }
}

function Install-ChocoPackage {
    param([string]$Name, [string]$Version, [string[]]$Extra = @())
    $arguments = @("install", $Name, "--version", $Version, "-y", "--no-progress",
                   "--execution-timeout=$ChocoTimeoutSeconds") + $Extra
    if ($WhatIf) { Write-Log "Would run: choco $($arguments -join ' ')"; return }
    Write-Log "Installing $Name $Version with Chocolatey"
    $code = Invoke-Native choco $arguments
    if ($code -eq 1641 -or $code -eq 3010) {
        Write-Log "$Name installed; Windows asks for a reboot to finish" "WARN"
    } elseif ($code -ne 0) {
        throw "choco install $Name failed with exit code $code (see $LogPath)"
    }
    Update-SessionPath
}

function Get-PythonVersion {
    param([string]$Exe)
    if (-not (Test-Path $Exe)) { return $null }
    try { $text = & $Exe -c "import sys; print('%d.%d.%d' % sys.version_info[:3])" } catch { return $null }
    if ($LASTEXITCODE -ne 0 -or -not $text) { return $null }
    return [version]"$text".Trim()
}

function Find-Python313 {
    $candidates = @(Join-Path $PythonDir "python.exe")
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            $fromLauncher = & py -3.13 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $fromLauncher) { $candidates += "$fromLauncher".Trim() }
        } catch { }
    }
    foreach ($exe in $candidates) {
        $version = Get-PythonVersion $exe
        if ($version -and $version.Major -eq 3 -and $version.Minor -eq 13) { return $exe }
    }
    return $null
}

function Find-GnuPG {
    $dirs = @("${env:ProgramFiles(x86)}\GnuPG\bin", "$env:ProgramFiles\GnuPG\bin")
    $onPath = Get-Command gpg -ErrorAction SilentlyContinue
    if ($onPath) { $dirs += Split-Path $onPath.Source }
    foreach ($dir in $dirs) {
        $complete = (Test-Path "$dir\gpg.exe") -and (Test-Path "$dir\gpgconf.exe") -and
                    (Test-Path "$dir\gpg-connect-agent.exe")
        if (-not $complete) { continue }
        $first = & "$dir\gpg.exe" --version | Select-Object -First 1
        if ($LASTEXITCODE -eq 0 -and "$first" -match '(\d+)\.(\d+)') {
            if ([int]$Matches[1] -gt 2 -or ([int]$Matches[1] -eq 2 -and [int]$Matches[2] -ge 2)) { return $dir }
        }
    }
    return $null
}

function Add-MachinePath {
    param([string[]]$Dirs)
    $entries = @(([Environment]::GetEnvironmentVariable("Path", "Machine") -split ';') | Where-Object { $_ })
    $missing = @($Dirs | Where-Object {
        $wanted = $_.TrimEnd('\')
        -not ($entries | Where-Object { $_.TrimEnd('\') -ieq $wanted })
    })
    if ($missing.Count -eq 0) { Write-Log "Machine PATH already contains: $($Dirs -join ', ')"; return }
    if ($WhatIf) { Write-Log "Would append to the machine PATH: $($missing -join ', ')"; return }
    # Appended, not prepended: an existing default python (for example 3.14) stays the default.
    [Environment]::SetEnvironmentVariable("Path", (($entries + $missing) -join ';'), "Machine")
    Write-Log "Appended to the machine PATH: $($missing -join ', ')" "SUCCESS"
}

function Set-MachineVariable {
    param([string]$Name, [string]$Value)
    if ([Environment]::GetEnvironmentVariable($Name, "Machine") -eq $Value) {
        Write-Log "$Name is already $Value"
    } elseif ($WhatIf) {
        Write-Log "Would set $Name=$Value (machine)"
    } else {
        # .NET broadcasts WM_SETTINGCHANGE, so newly opened terminals see the change.
        [Environment]::SetEnvironmentVariable($Name, $Value, "Machine")
        Write-Log "Set $Name=$Value (machine)" "SUCCESS"
    }
    Set-Item -Path "Env:$Name" -Value $Value
}

try {
    Write-Log "YubiKey recovery: preparing Windows (log: $LogPath)$(if ($WhatIf) { ' [dry run]' })"
    if (-not $WhatIf -and -not (Test-Administrator)) {
        throw "Administrator rights are needed: run Prepare-Windows.bat, or this script from an elevated PowerShell"
    }

    Install-Chocolatey

    $python = Find-Python313
    if ($python) {
        Write-Log "Python $(Get-PythonVersion $python) found at $python" "SUCCESS"
    } else {
        Install-ChocoPackage $PythonPackage $PythonVersion @("--params", "/InstallDir:$PythonDir")
        if (-not $WhatIf) {
            $python = Find-Python313
            if (-not $python) { throw "Python 3.13 was installed but python.exe was not found in $PythonDir" }
            Write-Log "Python $(Get-PythonVersion $python) installed at $python" "SUCCESS"
        }
    }
    if (-not $python) { $python = Join-Path $PythonDir "python.exe" }   # dry run without Python yet

    $gnupg = Find-GnuPG
    if ($gnupg) {
        Write-Log "GnuPG found in $gnupg" "SUCCESS"
    } else {
        try {
            Install-ChocoPackage $GnupgPackage $GnupgVersion
        } catch {
            throw "$($_.Exception.Message). Install Gpg4win from https://gpg4win.org instead, then run this again."
        }
        if (-not $WhatIf) {
            $gnupg = Find-GnuPG
            if (-not $gnupg) { throw "GnuPG was installed but gpg.exe was not found; install Gpg4win from https://gpg4win.org" }
            Write-Log "GnuPG installed in $gnupg" "SUCCESS"
        }
    }

    $pathDirs = @((Split-Path $python), (Join-Path (Split-Path $python) "Scripts"))
    if ($gnupg) { $pathDirs += $gnupg }
    Add-MachinePath $pathDirs
    Set-MachineVariable "YKRECOVER_PYTHON" $python
    Update-SessionPath

    if (-not $WhatIf) {
        Write-Log "Python: $(& $python --version 2>&1)"
        Write-Log "GnuPG:  $(& "$gnupg\gpg.exe" --version | Select-Object -First 1)"
        Write-Log "GnuPG home: $(& "$gnupg\gpgconf.exe" --list-dirs homedir)"
    }

    if ($Build) {
        if ($WhatIf) {
            Write-Log "Would run build-windows.ps1"
        } else {
            Write-Log "Building the executables"
            & (Join-Path $PSScriptRoot "build-windows.ps1")
            Write-Log "Built $(Join-Path $PSScriptRoot 'dist\ykrecover-windows-x64.zip')" "SUCCESS"
        }
    }

    Write-Log "Done. Open a new terminal to use the updated PATH." "SUCCESS"
    exit 0
} catch {
    Write-Log $_.Exception.Message "ERROR"
    exit 1
}
