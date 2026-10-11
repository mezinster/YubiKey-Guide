<#
.SYNOPSIS
Prepares a Windows machine to build and run the YubiKey recovery tool.

.DESCRIPTION
Installs Chocolatey if missing, then Python 3.13 and GnuPG through Chocolatey (pinned versions; skipped when
already installed under Program Files), puts their folders on the machine PATH and sets YKRECOVER_PYTHON so
build-windows.ps1 always builds with Python 3.13. With -Build it then builds the executables.

Run Prepare-Windows.bat (double-click), or:
  powershell -NoProfile -ExecutionPolicy Bypass -File prepare-windows.ps1 [-Build] [-WhatIf]

Started without administrator rights, the script elevates only the installation step (one UAC prompt) and
runs -Build afterwards as the normal user. The elevated step never runs a program from a folder that a
non-administrator can change: Chocolatey is called by its full path, and only Python and GnuPG under
Program Files are accepted and put on the machine PATH. Chocolatey itself is installed from a pinned package
whose SHA-256 is checked, unpacked into a folder only administrators can change.

-WhatIf only reports what would change and does not need administrator rights.
A log is written to logs\prepare_<date>.log next to this script.
#>
[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$WhatIf,
    [string]$LogPath,
    [switch]$Elevated   # internal: set on the elevated copy started for the installation step
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Pinned Chocolatey and packages (newest releases at least 14 days old on 2026-10-11). The Chocolatey
# package's SHA-256 matches the SHA-512 the community repository lists for it.
$ChocolateyVersion = "2.7.4"
$ChocolateyUrl = "https://community.chocolatey.org/api/v2/package/chocolatey/$ChocolateyVersion"
$ChocolateySha256 = "AEC2DBD6CE91B7378EB4A0C21524E748D30332EA856E898404999F77328BD504"
$PythonPackage = "python313"
$PythonVersion = "3.13.15"
$PythonDir = Join-Path $env:ProgramFiles "Python313"   # only administrators can change Program Files
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

function Update-SessionEnvironment {
    # Machine PATH first, then User PATH: the same order a new shell gets.
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
    $python = [Environment]::GetEnvironmentVariable("YKRECOVER_PYTHON", "Machine")
    if ($python) { $env:YKRECOVER_PYTHON = $python }
}

function Get-ChocoExe {
    # Always the full path under ProgramData (administrator-only), never a "choco" found on PATH.
    $root = $env:ChocolateyInstall
    if (-not $root) { $root = [Environment]::GetEnvironmentVariable("ChocolateyInstall", "Machine") }
    if (-not $root) { $root = Join-Path $env:ProgramData "chocolatey" }
    $exe = Join-Path $root "bin\choco.exe"
    if (Test-Path $exe) { return $exe }
    return $null
}

function Invoke-Native {
    # Runs a native command, copies its output to the console and the log, and returns its exit code.
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments | ForEach-Object { Write-Host "  $_"; Add-Content -Path $LogPath -Value "  $_" -Encoding UTF8 }
    return $LASTEXITCODE
}

function Get-FirstLine {
    # Runs a program to completion and returns its exit code and first output line. Piping a native
    # program into Select-Object -First stops it early in Windows PowerShell 5.1 and spoils $LASTEXITCODE.
    param([string]$Exe, [string[]]$Arguments)
    $ErrorActionPreference = "Continue"   # stderr from a native program must not become a terminating error
    $output = @(& $Exe @Arguments 2>$null)
    $code = $LASTEXITCODE
    $first = if ($output.Count) { "$($output[0])".Trim() } else { "" }
    return @{ Code = $code; Line = $first }
}

function New-AdminOnlyFolder {
    # A new folder under ProgramData that only Administrators and SYSTEM can change. The ACL is set when the
    # folder is created, so a non-administrator never gets a chance to put files into it.
    $path = Join-Path $env:ProgramData ("ykrecover-{0}" -f [guid]::NewGuid())
    if (Test-Path $path) { throw "$path already exists" }
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in "S-1-5-32-544", "S-1-5-18") {   # Administrators, SYSTEM
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            (New-Object System.Security.Principal.SecurityIdentifier $sid), "FullControl",
            "ContainerInherit,ObjectInherit", "None", "Allow")))
    }
    [void][System.IO.Directory]::CreateDirectory($path, $acl)
    return $path
}

function Install-Chocolatey {
    $choco = Get-ChocoExe
    if ($choco) {
        $version = (Get-FirstLine $choco @("--version")).Line
        Write-Log "Chocolatey $version is installed ($choco)"
        if ([version]($version -replace '[^0-9.].*$', '') -lt [version]"2.0.0") {
            if ($WhatIf) { Write-Log "Would upgrade Chocolatey to $ChocolateyVersion" }
            else {
                Write-Log "Upgrading Chocolatey to $ChocolateyVersion"
                $arguments = @("upgrade", "chocolatey", "--version", $ChocolateyVersion, "-y", "--no-progress")
                if ((Invoke-Native $choco $arguments) -ne 0) {
                    throw "Chocolatey upgrade failed (see $LogPath)"
                }
            }
        }
        Repair-ChocolateySource $choco
        return $choco
    }
    if ($WhatIf) {
        Write-Log "Would install Chocolatey $ChocolateyVersion from $ChocolateyUrl (SHA-256 $ChocolateySha256)"
        return $null
    }
    # Chocolatey's documented offline install: run tools\chocolateyInstall.ps1 from the extracted package.
    Write-Log "Installing Chocolatey $ChocolateyVersion from $ChocolateyUrl"
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
    $work = New-AdminOnlyFolder
    try {
        $package = Join-Path $work "chocolatey.$ChocolateyVersion.nupkg"
        (New-Object Net.WebClient).DownloadFile($ChocolateyUrl, $package)
        $hash = (Get-FileHash $package -Algorithm SHA256).Hash
        if ($hash -ne $ChocolateySha256) { throw "Chocolatey package hash mismatch: $hash (expected $ChocolateySha256)" }
        Write-Log "Chocolatey package verified (SHA-256 $hash)"
        $extracted = Join-Path $work "package"
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($package, $extracted)
        $installer = Join-Path $extracted "tools\chocolateyInstall.ps1"
        if (-not (Test-Path $installer)) { throw "tools\chocolateyInstall.ps1 missing from the Chocolatey package" }
        # Chocolatey's installer is not written for strict mode: run it in its own scope with strict mode off.
        & { Set-StrictMode -Off; & $installer } | ForEach-Object {
            Write-Host "  $_"; Add-Content -Path $LogPath -Value "  $_" -Encoding UTF8
        }
    } finally {
        Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
    }
    Update-SessionEnvironment
    $choco = Get-ChocoExe
    if (-not $choco) { throw "Chocolatey installation failed (see $LogPath)" }
    Write-Log "Chocolatey $((Get-FirstLine $choco @("--version")).Line) installed ($choco)" "SUCCESS"
    return $choco
}

function Repair-ChocolateySource {
    # Old installs point at chocolatey.org/api/v2, which no longer serves packages.
    param([string]$Choco)
    foreach ($line in @(& $Choco source list --limit-output)) {
        $fields = "$line" -split '\|'
        if ($fields.Count -ge 2 -and $fields[1] -match '^https?://chocolatey\.org/api/v2') {
            if ($WhatIf) { Write-Log "Would point Chocolatey source '$($fields[0])' at community.chocolatey.org"; continue }
            Write-Log "Pointing Chocolatey source '$($fields[0])' at community.chocolatey.org" "WARN"
            [void](Invoke-Native $Choco @("source", "remove", "-n=$($fields[0])"))
            [void](Invoke-Native $Choco @("source", "add", "-n=$($fields[0])", "-s=https://community.chocolatey.org/api/v2/"))
        }
    }
}

function Install-ChocoPackage {
    param([string]$Choco, [string]$Name, [string]$Version, [string[]]$Extra = @())
    $arguments = @("install", $Name, "--version", $Version, "-y", "--no-progress",
                   "--execution-timeout=$ChocoTimeoutSeconds") + $Extra
    if ($WhatIf -or -not $Choco) { Write-Log "Would run: choco $($arguments -join ' ')"; return }
    Write-Log "Installing $Name $Version with Chocolatey"
    $code = Invoke-Native $Choco $arguments
    if ($code -eq 1641 -or $code -eq 3010) {
        Write-Log "$Name installed; Windows asks for a reboot to finish" "WARN"
    } elseif ($code -ne 0) {
        throw "choco install $Name failed with exit code $code (see $LogPath)"
    }
}

function Get-InstalledPython {
    # Only the Program Files install is considered; never runs a python from a user-writable folder.
    $exe = Join-Path $PythonDir "python.exe"
    if (-not (Test-Path $exe)) { return $null }
    $text = & $exe -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
    if ($LASTEXITCODE -ne 0 -or "$text" -notmatch '^3\.13\.') { return $null }
    return $exe
}

function Get-InstalledGnuPG {
    # Only Program Files installs (Gpg4win or GnuPG) are considered.
    foreach ($dir in @("${env:ProgramFiles(x86)}\GnuPG\bin", "$env:ProgramFiles\GnuPG\bin")) {
        $complete = (Test-Path "$dir\gpg.exe") -and (Test-Path "$dir\gpgconf.exe") -and
                    (Test-Path "$dir\gpg-connect-agent.exe")
        if (-not $complete) { continue }
        $result = Get-FirstLine "$dir\gpg.exe" @("--version")
        Write-Log "Found $dir\gpg.exe: $($result.Line) (exit code $($result.Code))"
        if ($result.Code -eq 0 -and $result.Line -match '(\d+)\.(\d+)') {
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
    # .NET broadcasts WM_SETTINGCHANGE, so newly opened terminals see the change.
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
        [Environment]::SetEnvironmentVariable($Name, $Value, "Machine")
        Write-Log "Set $Name=$Value (machine)" "SUCCESS"
    }
}

function Invoke-Installation {
    # Everything that needs administrator rights. Every program it runs lives under ProgramData or
    # Program Files, which non-administrators cannot change.
    $choco = Install-Chocolatey

    $python = Get-InstalledPython
    if ($python) {
        Write-Log "Python 3.13 found at $python" "SUCCESS"
    } else {
        Install-ChocoPackage $choco $PythonPackage $PythonVersion @("--params", "'/InstallDir:$PythonDir'")
        if (-not $WhatIf) {
            $python = Get-InstalledPython
            if (-not $python) { throw "Python 3.13 was installed but $PythonDir\python.exe was not found" }
            Write-Log "Python installed at $python" "SUCCESS"
        }
    }
    if (-not $python) { $python = Join-Path $PythonDir "python.exe" }   # dry run without Python yet

    $gnupg = Get-InstalledGnuPG
    if ($gnupg) {
        Write-Log "GnuPG found in $gnupg" "SUCCESS"
    } else {
        try {
            Install-ChocoPackage $choco $GnupgPackage $GnupgVersion
        } catch {
            throw "$($_.Exception.Message). Install Gpg4win from https://gpg4win.org instead, then run this again."
        }
        if (-not $WhatIf) {
            $gnupg = Get-InstalledGnuPG
            if (-not $gnupg) { throw "GnuPG was installed but not found under Program Files; install Gpg4win from https://gpg4win.org" }
            Write-Log "GnuPG installed in $gnupg" "SUCCESS"
        }
    }

    $dirs = @((Split-Path $python), (Join-Path (Split-Path $python) "Scripts"))
    if ($gnupg) { $dirs += $gnupg }
    Add-MachinePath $dirs
    Set-MachineVariable "YKRECOVER_PYTHON" $python

    if (-not $WhatIf) {
        Write-Log "Python: $(& $python --version 2>&1)"
        Write-Log "GnuPG:  $((Get-FirstLine "$gnupg\gpg.exe" @("--version")).Line)"
    }
}

function Invoke-ElevatedInstallation {
    # Runs this script's installation step as administrator (UAC prompt) and waits for it.
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Elevated -LogPath `"$LogPath`""
    Write-Log "Asking for administrator rights to install Chocolatey, Python and GnuPG"
    try {
        $process = Start-Process -FilePath (Join-Path $PSHOME "powershell.exe") -ArgumentList $arguments `
                                 -Verb RunAs -PassThru
    } catch {
        throw "Administrator rights were not granted: $($_.Exception.Message)"
    }
    $process.WaitForExit()   # only this process: installers may leave background agents running
    if ($process.ExitCode -ne 0) { throw "The installation step failed (exit code $($process.ExitCode)); see $LogPath" }
}

try {
    if ($Elevated) {
        if (-not (Test-Administrator)) { throw "The elevated installation step is not running as administrator" }
        Write-Log "Installation step running as administrator"
        Invoke-Installation
        exit 0
    }

    Write-Log "YubiKey recovery: preparing Windows (log: $LogPath)$(if ($WhatIf) { ' [dry run]' })"
    if ($WhatIf -or (Test-Administrator)) {
        Invoke-Installation
    } else {
        Invoke-ElevatedInstallation
    }
    Update-SessionEnvironment

    if ($Build) {
        if ($WhatIf) {
            Write-Log "Would run build-windows.ps1 as the current user"
        } elseif (Test-Administrator) {
            # The build runs Python from the repository's .venv, a folder normal users can change.
            Write-Log ("Not building as administrator. Open a normal (non-elevated) prompt and run " +
                       "build-windows.ps1, or run Prepare-Windows.bat -Build without 'Run as administrator'.") "WARN"
        } else {
            Write-Log "Building the executables as $env:USERNAME"
            # A separate process, so the build script does not inherit this script's strict mode.
            & (Join-Path $PSHOME "powershell.exe") -NoProfile -ExecutionPolicy Bypass `
                -File (Join-Path $PSScriptRoot "build-windows.ps1")
            if ($LASTEXITCODE -ne 0) { throw "build-windows.ps1 failed with exit code $LASTEXITCODE" }
            Write-Log "Built $(Join-Path $PSScriptRoot 'dist\ykrecover-windows-x64.zip')" "SUCCESS"
        }
    }

    Write-Log "Done. Open a new terminal to use the updated PATH." "SUCCESS"
    exit 0
} catch {
    Write-Log $_.Exception.Message "ERROR"
    if ($Elevated) { Read-Host "The installation step failed (details above). Press Enter to close" | Out-Null }
    exit 1
}
