YubiKey recovery for Windows
============================

Migrates GnuPG key backups onto new YubiKeys. Full documentation:
https://github.com/mezinster/YubiKey-Guide/blob/master/recovery/README.md

1. Install Gpg4win from https://gpg4win.org (it provides gpg, gpgconf and gpg-connect-agent).
2. Unzip this archive anywhere.
3. Double-click ykrecover-gui.exe for the step-by-step wizard, or run
   ykrecover.exe --cli --help
   in a terminal for the command-line version.

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

Before using real backups, run the hardware checklist in recovery/README.md with a spare YubiKey on
this Windows machine.
