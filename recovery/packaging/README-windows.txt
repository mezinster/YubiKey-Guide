YubiKey recovery for Windows
============================

Migrates GnuPG key backups onto new YubiKeys. Full documentation:
https://github.com/mezinster/YubiKey-Guide/blob/master/recovery/README.md

1. Install GnuPG: either Gpg4win from https://gpg4win.org, or run Prepare-Windows.bat from the
   repository's recovery\packaging folder (installs Chocolatey, GnuPG and Python 3.13 and sets PATH).
2. Unzip this archive anywhere. It holds one folder, ykrecover-windows-x64; keep its contents together
   (ykrecover.exe and ykrecover-gui.exe both need the _internal folder next to them).
3. In that folder, double-click ykrecover-gui.exe for the step-by-step wizard, or run
   ykrecover.exe --cli --help
   in a terminal for the command-line version. Use cmd, PowerShell or Windows Terminal: Git Bash
   (mintty) shows the passphrase as you type it, because its input is not a real console.

Windows SmartScreen may warn that the program is unrecognised: these executables are not code-signed.
Choose "More info" and "Run anyway" if you trust this build.

Key material on disk
--------------------
The tool copies your backup into temporary GnuPG folders while it works. On Linux those live in memory;
on Windows they default to %LOCALAPPDATA%\Temp\ykrecover on disk and are overwritten and deleted at the
end. To keep key material off the disk, create a RAM disk (for example with ImDisk) or use an encrypted
volume, and choose it as the "Work folder" in the wizard (or pass --workdir DIR on the command line).

If a run ends abnormally (the console window is closed, the process is ended in Task Manager, or it
crashes), its temporary folders (named ykr-*) can stay behind in the work folder without being
overwritten. The next run warns about any that are older than 10 minutes: they may hold key material,
so delete them securely (for example with Sysinternals SDelete).

A work folder on a RAM disk or VeraCrypt volume inherits that volume's permissions. On a shared
machine, restrict the folder to your own user account before using it.

The Admin PIN is passed to the bundled ykman on its command line. Process-creation auditing or endpoint
security software on a managed or audited machine can record command lines: on such a machine, change
the Admin PIN afterwards (ykrecover.exe --ykman openpgp access change-admin-pin).

Before using real backups, run the hardware checklist in recovery/README.md with a spare YubiKey on
this Windows machine.
