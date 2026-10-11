# YubiKey Recovery on Windows — Design

Date: 2026-10-11
Status: approved in brainstorming, pending spec review
Builds on: `docs/superpowers/specs/2026-10-11-yubikey-recovery-design.md` (the Linux toolset). Everything there still applies unless this document says otherwise.

## Purpose

Run the Python recovery tool (`recovery/ykrecover`) on Windows as a standalone binary: no Python install and no separate ykman install, started by double-click or from a terminal.

### Success criteria

- On Windows 10/11 x64 with Gpg4win installed, `ykrecover-gui.exe` (double-click) and `ykrecover.exe --cli …` perform clone/renew/rotate, card verification and `update-stubs` exactly as on Linux.
- `ykrecover.exe --cli … --dry-run` prints the same plan as Linux; CI checks it against the golden plans.
- Linux behaviour, the bash script, the golden plans and the existing tests are unchanged.

### Decisions (from brainstorming)

- **GnuPG** comes from an installed Gpg4win. It is not bundled.
- **ykman is bundled** inside the executable (yubikey-manager plus its pyscard dependency). It is reached through a hidden `--ykman` entry point (approach A).
- **Temporary key copies** go to disk by default on Windows, with a clear warning. `--workdir` (CLI and bash) and a GUI "Work folder" field point them at a RAM disk or an encrypted volume. Files are overwritten with zeros before they are deleted, as a best effort.
- **Delivery:** a CI job on `windows-latest` tests and builds on every push and uploads the zip as a workflow artifact. A tag `v*` attaches the zip to a GitHub Release. A local build script is also provided.

### Scope

- In: Windows 10/11 x64, the Python GUI and CLI, `update-stubs`.
- Out: the bash script on Windows, macOS, ARM64, code signing, an installer and auto-update.

### Assumptions

- Windows is likely the user's everyday, networked machine. The tool warns about this (network up, disk-backed workdir) and does not refuse to run.

## Components

### `recovery/ykrecover/osdeps.py` (new): the only platform-specific module

| Function | Linux | Windows |
| - | - | - |
| `tool(name) -> list[str]` | `[name]` | `gpg`, `gpgconf`, `gpg-connect-agent`: `shutil.which(name)`, then `%ProgramFiles(x86)%\GnuPG\bin\<name>.exe`, then `%ProgramFiles%\GnuPG\bin\<name>.exe`; missing → `RecoveryError(EXIT_USAGE, "Gpg4win not found …")`. `ykman`: frozen → `[<dir of sys.executable>\ykrecover.exe, "--ykman"]` (the console executable, even when called from the GUI executable); from source → `[sys.executable, "-m", "ykman"]`. Other names → `shutil.which(name) or name`. |
| `work_base(workdir: Path \| None) -> Path` | `workdir` if given; else today's rule (`$XDG_RUNTIME_DIR` if ≤ 60 chars, else `/tmp`) | `workdir` if given; else `%LOCALAPPDATA%\Temp\ykrecover` (created if missing) |
| `memory_backed(path) -> bool` | existing tmpfs check from `/proc/mounts` | `GetDriveTypeW(<drive root>) == DRIVE_RAMDISK` through ctypes; otherwise False |
| `network_up() -> list[str]` | existing `/sys/class/net` scan | adapter names that are operationally up, from `GetAdaptersAddresses` through ctypes; on any ctypes error, `[]` |
| `user_gnupg_home() -> Path` | `$GNUPGHOME` or `~/.gnupg` | `$GNUPGHOME` or `%APPDATA%\gnupg` |
| `secure_delete(path)` | `shutil.rmtree` | overwrite each regular file with zeros of the same length, then `shutil.rmtree`; errors are ignored like today |
| `subprocess_flags() -> dict` | `{}` | `{"creationflags": subprocess.CREATE_NO_WINDOW}`, so no console windows flash from the GUI |
| `supported() -> bool` | True | True |

On any other platform, `supported()` returns False.

Tool resolution happens only when a command is executed: `Home.run`, and the direct `subprocess.run` calls in `migrate.py` and `stubs.py`, replace `argv[0]` with `tool(argv[0])`. Plans keep the plain tool names, so dry-run output and the golden plans do not change. Preflight's tool-presence check uses `tool()` and reports a missing Gpg4win clearly.

### Changes to existing modules

- `gpg.py`:
  - `runtime_base()` becomes `osdeps.work_base(...)`, and `Home.create` uses it. The workdir comes from `Options.workdir` through a module-level setter that `Session` calls once.
  - `base_is_tmpfs` becomes `osdeps.memory_backed`.
  - `Home.destroy` kills the agents, then calls `osdeps.secure_delete`.
  - `Home.run` resolves the tool and passes `osdeps.subprocess_flags()`.
- `migrate.py`:
  - `Options.workdir: Path | None = None`.
  - Preflight uses `osdeps.network_up()` and `osdeps.memory_backed()`. On Windows the disk warning reads: "temporary key copies are on disk at <path>; use --workdir with a RAM disk or encrypted volume to avoid that".
- `stubs.py`: `user_home()` becomes `osdeps.user_gnupg_home()`.
- `cli.py`:
  - Adds `--workdir DIR`.
  - `getpass` is unchanged; Windows supports it on a console.
- `gui.py`: the Card settings page gets a "Work folder" entry with a Browse button and an inline warning when the folder is not memory-backed.
- `__main__.py`:
  - `--ykman ARGS…` hands off to `ykman._cli.__main__.main()`, with `sys.argv` rewritten to `["ykman", *ARGS]`.
  - The platform check uses `osdeps.supported()`.
  - The current Linux-only message becomes "Linux or Windows only".
- `recoverCard.sh`: adds `--workdir DIR`, so option parity is kept. When given, homes are created under it, and validation requires an existing directory. The bash script stays Linux-only.

### Packaging (`recovery/packaging/`, new)

- `requirements-windows.txt` pins exact versions of `yubikey-manager` and `pyinstaller`. Each version is the newest release at least 14 days old when the plan is written.
- `ykrecover.spec` (PyInstaller) builds two executables from one analysis:
  - `ykrecover.exe`: console mode, one file. Entry is `ykrecover.__main__`.
  - `ykrecover-gui.exe`: windowed mode, one file. Same entry, which starts the GUI.
  - Both bundle Tk, yubikey-manager, pyscard and the `ykrecover` package. The spec lists the hidden imports PyInstaller needs for ykman, as verified in CI.
- `build-windows.ps1`: creates a venv, installs the pinned requirements, runs PyInstaller, and assembles `dist\ykrecover-windows-x64.zip`. The zip holds both executables, `README-windows.txt` and `LICENSE`.
- `README-windows.txt`:
  1. Install Gpg4win.
  2. Run `ykrecover-gui.exe`, or `ykrecover.exe --cli --help`.
  3. The disk warning and how to use `--workdir`.
  4. Pointer to the hardware checklist in `recovery/README.md`.

### CI

- In `.github/workflows/lint.yml`, add a `windows` job on `windows-latest`:
  1. `choco install gpg4win -y`, then add the GnuPG `bin` folder to PATH.
  2. Set up Python 3.12.
  3. Install the pinned requirements.
  4. Run `python -m unittest discover -s tests -t . -v` in `recovery`.
  5. Run `build-windows.ps1`.
  6. Smoke test 1: `dist\ykrecover.exe --cli --backup tests\fixtures\ed25519\export --dry-run` output equals `tests\golden\clone-ed25519-export.plan`.
  7. Smoke test 2: `dist\ykrecover.exe --ykman --version` exits 0.
  8. Upload the zip with `actions/upload-artifact`.
- A new `.github/workflows/release-windows.yml` runs on tags `v*`. It repeats steps 1–7 on `windows-latest`, then attaches the zip to the GitHub Release for the tag using `gh release upload` (creating the release if needed). It needs permission `contents: write`.

## Testing

- **Fakes on Windows:** `helpers.fake_hardware` writes `<name>.cmd` wrappers into a temp bin folder. Each wrapper runs `"<python>" "<fakebin>\<name>" %*`. The helper puts that folder first on PATH. `tool()` checks `shutil.which` first, so PATH alone is how the fakes are found. The fake `gpg` reaches the real one through `REAL_GPG`, which points at Gpg4win's `gpg.exe`.
- **Skipped on Windows (`skipUnless(sys.platform.startswith("linux"))`):** the bash test classes and the signal tests (no SIGHUP; SIGTERM terminates without handlers).
- **New `tests/test_osdeps.py`:**
  - `tool()` resolution on both platforms, with the environment and `sys.platform` patched and a fake GnuPG folder in a temp dir;
  - the `ykman` mapping, frozen and from source;
  - the `work_base` rules;
  - `user_gnupg_home`;
  - `secure_delete` overwriting files before removing them;
  - `__main__ --ykman --version` dispatching to ykman. This test is skipped when yubikey-manager is not installed.
- **Linux:** the suite, golden plans and bash parity are unchanged. Bash `--workdir` gets a contract test.

## Risks

- **PyInstaller and ykman:** pyscard's native module and ykman's metadata lookups may need hooks. The CI smoke test (`--ykman --version`) catches this.
- **gpg-agent on Windows** uses a different socket emulation (Assuan sockets as files in the GnuPG home). `gpgconf --kill all` per home still applies. Deleting a home fails while its agent is alive, so the agents are killed before deletion and failures are tolerated.
- **Real cards on Windows:** scdaemon and ykman compete for the card through WinSCard, as they do through pcscd on Linux. The kill-before-every-session rule covers this. The manual hardware checklist must be run on Windows too; the README says so.
- **Unsigned executables** trigger SmartScreen warnings. This is documented in `README-windows.txt`.

## Decisions made during planning

- **Network check on Windows:** a UDP route probe to the TEST-NET address 192.0.2.1 (nothing is sent; `getsockname()` reveals a non-loopback source address when a route exists) instead of `GetAdaptersAddresses` through ctypes. Same purpose, far less ctypes code.
- **GUI work folder:** the field sits on the Backup page, not Card settings, because the staging home is created when the backup is loaded, before Card settings is shown.
- **ykman on Windows:** a `ykman` already on PATH (Yubico's installer, or the test fakes) is used before the bundled copy. From source, the bundled path is `python -c <shim>` calling `ykman._cli.__main__:main`; yubikey-manager has no `python -m ykman`.
- **`--workdir` on Linux** must also be at most 60 characters long (gpg-agent socket limit). Both implementations reject longer paths with exit 1.
- **Line endings:** `.gitattributes` marks fixtures and golden plans `-text` and keeps scripts LF, so Windows checkouts with `core.autocrlf` cannot corrupt them.
- **Fake gpg** passes through to the real gpg with `subprocess.run` instead of `os.execv` (portable).
- **Pins:** `yubikey-manager==5.9.2`, `pyinstaller==6.22.3`. Transitive dependencies are not pinned.
