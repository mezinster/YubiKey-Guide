<#
.SYNOPSIS
Installs the official GnuPG for Windows on a CI runner (lint.yml and release-windows.yml).

.DESCRIPTION
Pinned by version and SHA-256 (signature verified against the GnuPG release key
6DAA6E64A76D2840571B4902528897B826403ADA when the pin was set). The silent installer hangs on headless
runners, so its bin\ folder is unpacked with the preinstalled 7-Zip instead; GnuPG on Windows finds its
helper programs relative to gpg.exe. The default destination is where Gpg4win installs GnuPG, so the
recovery tool and prepare-windows.ps1 find it there; bin\ is also added to PATH for later steps.
#>
param([string]$Destination = (Join-Path $env:ProgramFiles "GnuPG"))

$ErrorActionPreference = "Stop"
$GnupgUrl = "https://gnupg.org/ftp/gcrypt/binary/gnupg-w32-2.5.24_20260923.exe"
$GnupgSha256 = "EA6F9DEE2CCCD83287432625B8B399173805ADD81D27067FCAF019C0C4DA1AD8"

$installer = Join-Path $env:RUNNER_TEMP "gnupg-w32.exe"
Write-Host "Downloading $GnupgUrl"
Invoke-WebRequest -Uri $GnupgUrl -OutFile $installer
$hash = (Get-FileHash $installer -Algorithm SHA256).Hash
if ($hash -ne $GnupgSha256) { throw "GnuPG installer hash mismatch: $hash" }
& "$env:ProgramFiles\7-Zip\7z.exe" x $installer "-o$Destination" -y | Out-Null
if ($LASTEXITCODE -ne 0) { throw "7-Zip could not unpack the GnuPG installer ($LASTEXITCODE)" }
Remove-Item "$Destination\bin\*.tmp" -ErrorAction SilentlyContinue
foreach ($tool in "gpg", "gpgconf", "gpg-connect-agent", "gpg-agent", "scdaemon") {
    if (-not (Test-Path "$Destination\bin\$tool.exe")) { throw "$tool.exe missing from the GnuPG installer" }
}
& "$Destination\bin\gpg.exe" --version | Select-Object -First 2
if ($LASTEXITCODE -ne 0) { throw "gpg --version failed" }
& "$Destination\bin\gpgconf.exe" --list-dirs
"$Destination\bin" | Out-File -FilePath $env:GITHUB_PATH -Append -Encoding utf8
