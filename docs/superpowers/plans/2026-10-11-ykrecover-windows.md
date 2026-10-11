# YubiKey Recovery on Windows — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the Python recovery tool (`recovery/ykrecover`) on Windows 10/11 x64 as two bundled executables (`ykrecover.exe`, `ykrecover-gui.exe`) built by CI, with GnuPG taken from an installed Gpg4win.

**Architecture:**

- A new `ykrecover/osdeps.py` holds every platform difference:
  - tool resolution, including the bundled ykman;
  - the temporary-home location and `--workdir`;
  - memory-backed and network checks;
  - the user's GnuPG home;
  - secure deletion;
  - one `run()` subprocess wrapper.
- The rest of the package calls osdeps instead of `subprocess`, `/tmp` or `/proc` directly.
- Plans keep plain tool names, so dry-run output and the golden plans are unchanged.
- PyInstaller builds both executables from one analysis. `ykrecover.exe --ykman …` runs the bundled ykman CLI.

**Tech Stack:** Python ≥ 3.11 (stdlib; `ctypes`/`socket` for Windows checks), yubikey-manager 5.9.2 (bundled), PyInstaller 6.22.3, Gpg4win, GitHub Actions `windows-latest`.

**Spec:** `docs/superpowers/specs/2026-10-11-ykrecover-windows-design.md` (builds on `docs/superpowers/specs/2026-10-11-yubikey-recovery-design.md`)

## Global Constraints

- **Linux behaviour must not change:**
  - golden plans in `recovery/tests/golden/` stay byte-identical;
  - the bash script keeps its contract;
  - the existing Linux test suite passes.
- **Runtime:** `recovery/ykrecover` remains stdlib-only. yubikey-manager is imported only by the `--ykman` redirect (lazily) and bundled only into the Windows executables.
- **Pinned build requirements:**
  - `yubikey-manager==5.9.2`
  - `pyinstaller==6.22.3`
  Both were the newest PyPI releases at least 14 days old on 2026-10-11.
- **Platforms:** Linux and Windows (`sys.platform == "win32"`). Anything else exits 1 with "ykrecover runs on Linux or Windows only".
- **Gpg4win lookup order** for `gpg`, `gpgconf`, `gpg-connect-agent`:
  1. `shutil.which(name)`;
  2. `%ProgramFiles(x86)%\GnuPG\bin\<name>.exe`;
  3. `%ProgramFiles%\GnuPG\bin\<name>.exe`.
  If none is found: `RecoveryError("<name> not found: install Gpg4win (https://gpg4win.org)", EXIT_USAGE)`.
- **ykman on Windows:**
  - `shutil.which("ykman")` if found;
  - else, frozen: `[<dir of sys.executable>\ykrecover.exe, "--ykman"]`;
  - else, from source: `[sys.executable, "-c", YKMAN_SHIM]`.
  - On Linux, every tool resolves to `[name]`, as today.
- **Default temp base:**
  - Windows: `%LOCALAPPDATA%\Temp\ykrecover` (created on demand);
  - Linux: unchanged (`$XDG_RUNTIME_DIR` if it is at most 60 characters, else `/tmp`).
  - `--workdir DIR` overrides both. It must be an existing directory; on Linux the resolved path must be at most 60 characters. Otherwise exit 1.
- **Disk warning text:** "temporary key copies are on disk at <base>; use --workdir with a RAM disk or encrypted volume to avoid that".
- **Subprocesses:** every one goes through `osdeps.run()`. It decodes text as UTF-8 (`errors="replace"`) and uses `CREATE_NO_WINDOW` on Windows.
- **Release zip:** `ykrecover-windows-x64.zip` contains `ykrecover.exe`, `ykrecover-gui.exe`, `README-windows.txt` and `LICENSE`.
- **Tests:** run from `recovery/` with `python3 -m unittest discover -s tests -t . -v`.

## Review Focus

1. **Gpg4win missing entirely.** Expected: preflight exits 1 with "gpg not found: install Gpg4win (<https://gpg4win.org>)" and no traceback. Pinned by Task 1 `test_missing_gpg4win_raises_clear_error` and Task 2 `test_preflight_reports_missing_gpg4win`.
2. **Gpg4win installed but not on PATH** (the default for Gpg4win GUI installs on some systems). Expected: the tool still finds `gpg.exe` in Program Files. Pinned by Task 1 `test_windows_finds_gpg4win_outside_path`.
3. **`--workdir` too long on Linux** (gpg-agent socket limit). Expected: exit 1 up front, before gpg fails with an obscure agent error. Pinned by Task 2 `test_workdir_must_exist_and_be_short` (both implementations).
4. **Temp home holding read-only files on Windows** (copied from read-only backup media). Expected: deletion still succeeds. Pinned by Task 1 `test_secure_delete_removes_read_only_files`.
5. **Non-ASCII gpg output on a cp1252 Windows console** (UIDs with umlauts). Expected: decoded without `UnicodeDecodeError`. Pinned by Task 1 `test_run_decodes_utf8_output`.

---

### Task 1: `osdeps.py`, the platform layer

**Files:**

- Create: `recovery/ykrecover/osdeps.py`
- Test: `recovery/tests/test_osdeps.py`

**Interfaces:**

- Consumes: `ykrecover.RecoveryError`, `EXIT_USAGE`.
- Produces:
  - `is_windows() -> bool` and `supported() -> bool`;
  - `MAX_BASE_LEN = 60`, `GNUPG_TOOLS`, `YKMAN_SHIM: str`;
  - `tool(name) -> list[str]` (raises `RecoveryError(EXIT_USAGE)` if a GnuPG tool is missing on Windows);
  - `available(name) -> bool`;
  - `set_workdir(path: Path | None)` and `work_base() -> Path`;
  - `memory_backed(path) -> bool` and `network_up() -> list[str]`;
  - `user_gnupg_home() -> Path` and `secure_delete(path)`;
  - `run(argv, input=None, env=None) -> CompletedProcess`: text, UTF-8, captured, never raises on a non-zero exit.
- Platform checks read `sys.platform` on every call (not at import), so tests can patch it.

- [ ] **Step 1: Write the failing tests**

`recovery/tests/test_osdeps.py`:

```python
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ykrecover import EXIT_USAGE, RecoveryError, osdeps


def win(test):
    """Pretend to be Windows for the duration of a test."""
    patcher = mock.patch.object(osdeps.sys, "platform", "win32")
    patcher.start()
    test.addCleanup(patcher.stop)


class ToolTests(unittest.TestCase):
    def test_linux_tools_are_plain_names(self):
        with mock.patch.object(osdeps.sys, "platform", "linux"):
            for name in ("gpg", "gpgconf", "gpg-connect-agent", "ykman"):
                self.assertEqual(osdeps.tool(name), [name])

    def test_windows_finds_gpg4win_outside_path(self):
        win(self)
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        (root / "GnuPG" / "bin").mkdir(parents=True)
        exe = root / "GnuPG" / "bin" / "gpgconf.exe"
        exe.write_text("")
        env = {"ProgramFiles(x86)": str(root / "missing"), "ProgramFiles": str(root)}
        with mock.patch.dict(os.environ, env), mock.patch.object(osdeps.shutil, "which", return_value=None):
            self.assertEqual(osdeps.tool("gpgconf"), [str(exe)])

    def test_windows_prefers_path(self):
        win(self)
        with mock.patch.object(osdeps.shutil, "which", return_value=r"C:\bin\gpg.exe"):
            self.assertEqual(osdeps.tool("gpg"), [r"C:\bin\gpg.exe"])

    def test_missing_gpg4win_raises_clear_error(self):
        win(self)
        with mock.patch.dict(os.environ, {"ProgramFiles(x86)": "", "ProgramFiles": ""}), \
                mock.patch.object(osdeps.shutil, "which", return_value=None):
            with self.assertRaises(RecoveryError) as ctx:
                osdeps.tool("gpg")
            self.assertEqual(ctx.exception.code, EXIT_USAGE)
            self.assertIn("install Gpg4win", str(ctx.exception))
            self.assertFalse(osdeps.available("gpg"))

    def test_windows_ykman_is_bundled(self):
        win(self)
        with mock.patch.object(osdeps.shutil, "which", return_value=None):
            with mock.patch.object(osdeps.sys, "frozen", True, create=True), \
                    mock.patch.object(osdeps.sys, "executable", r"C:\app\ykrecover-gui.exe"):
                self.assertEqual(osdeps.tool("ykman"), [str(Path(r"C:\app\ykrecover-gui.exe").with_name(
                    "ykrecover.exe")), "--ykman"])
            self.assertEqual(osdeps.tool("ykman"), [sys.executable, "-c", osdeps.YKMAN_SHIM])


class WorkBaseTests(unittest.TestCase):
    def tearDown(self):
        osdeps.set_workdir(None)

    def test_workdir_overrides(self):
        osdeps.set_workdir(Path("/some/where"))
        self.assertEqual(osdeps.work_base(), Path("/some/where"))

    def test_linux_default(self):
        with mock.patch.object(osdeps.sys, "platform", "linux"), \
                mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": ""}):
            self.assertEqual(osdeps.work_base(), Path("/tmp"))

    def test_windows_default_is_created_under_localappdata(self):
        win(self)
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(root)}):
            base = osdeps.work_base()
        self.assertEqual(base, root / "Temp" / "ykrecover")
        self.assertTrue(base.is_dir())


class HomeAndRunTests(unittest.TestCase):
    def test_user_gnupg_home(self):
        with mock.patch.dict(os.environ, {"GNUPGHOME": "/x/gnupg"}):
            self.assertEqual(osdeps.user_gnupg_home(), Path("/x/gnupg"))
        env = {k: v for k, v in os.environ.items() if k != "GNUPGHOME"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(osdeps.sys, "platform", "linux"):
                self.assertEqual(osdeps.user_gnupg_home(), Path.home() / ".gnupg")
            with mock.patch.object(osdeps.sys, "platform", "win32"), \
                    mock.patch.dict(os.environ, {"APPDATA": "/appdata"}):
                self.assertEqual(osdeps.user_gnupg_home(), Path("/appdata") / "gnupg")

    def test_secure_delete_removes_read_only_files(self):
        root = Path(tempfile.mkdtemp())
        (root / "sub").mkdir()
        f = root / "sub" / "secret.key"
        f.write_bytes(b"secret")
        f.chmod(stat.S_IREAD)
        osdeps.secure_delete(root)
        self.assertFalse(root.exists())

    def test_secure_delete_overwrites_on_windows(self):
        win(self)
        root = Path(tempfile.mkdtemp())
        f = root / "secret.key"
        f.write_bytes(b"secret")
        seen = []
        real_rmtree = osdeps.shutil.rmtree

        def spy(path, **kwargs):
            seen.append(f.read_bytes())
            real_rmtree(path, **kwargs)

        with mock.patch.object(osdeps.shutil, "rmtree", spy):
            osdeps.secure_delete(root)
        self.assertEqual(seen, [b"\0" * 6])

    def test_run_decodes_utf8_output(self):
        code = "import sys; sys.stdout.buffer.write('Jürgen Müller\\n'.encode('utf-8'))"
        with mock.patch.object(osdeps, "tool", lambda name: [name]):
            result = osdeps.run([sys.executable, "-c", code])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "Jürgen Müller\n")

    def test_run_resolves_the_tool(self):
        with mock.patch.object(osdeps, "tool", return_value=[sys.executable, "-c"]) as tool:
            result = osdeps.run(["fake", "print('ok')"])
        tool.assert_called_once_with("fake")
        self.assertEqual(result.stdout.strip(), "ok")

    def test_memory_backed_and_network_never_raise(self):
        self.assertIsInstance(osdeps.memory_backed(Path(tempfile.gettempdir())), bool)
        self.assertIsInstance(osdeps.network_up(), list)

    def test_supported(self):
        for platform, expected in (("linux", True), ("win32", True), ("darwin", False)):
            with mock.patch.object(osdeps.sys, "platform", platform):
                self.assertEqual(osdeps.supported(), expected)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd recovery && python3 -m unittest tests.test_osdeps -v`
Expected: ERROR, `ImportError: cannot import name 'osdeps'`.

- [ ] **Step 3: Implement `osdeps.py`**

`recovery/ykrecover/osdeps.py`:

```python
"""Everything that differs between Linux and Windows. Platform checks read sys.platform per call."""

import ctypes
import os
import shutil
import socket
import stat
import subprocess
import sys
from pathlib import Path

from . import EXIT_USAGE, RecoveryError

MAX_BASE_LEN = 60  # gpg-agent socket paths on Linux must stay well under the 108-byte limit
GNUPG_TOOLS = ("gpg", "gpgconf", "gpg-connect-agent")
YKMAN_SHIM = "import sys; sys.argv[0] = 'ykman'; from ykman._cli.__main__ import main; sys.exit(main())"
DRIVE_RAMDISK = 6
_workdir = None


def is_windows() -> bool:
    return sys.platform == "win32"


def supported() -> bool:
    return sys.platform.startswith("linux") or is_windows()


def _gpg4win_dirs() -> list:
    return [Path(base) / "GnuPG" / "bin"
            for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")) if base]


def tool(name: str) -> list:
    """The command line that runs the tool a plan calls `name`."""
    if not is_windows():
        return [name]
    found = shutil.which(name)
    if found:
        return [found]
    if name in GNUPG_TOOLS:
        for folder in _gpg4win_dirs():
            exe = folder / f"{name}.exe"
            if exe.is_file():
                return [str(exe)]
        raise RecoveryError(f"{name} not found: install Gpg4win (https://gpg4win.org)", EXIT_USAGE)
    if name == "ykman":
        if getattr(sys, "frozen", False):
            return [str(Path(sys.executable).with_name("ykrecover.exe")), "--ykman"]
        return [sys.executable, "-c", YKMAN_SHIM]
    return [name]


def available(name: str) -> bool:
    if not is_windows():
        return shutil.which(name) is not None
    try:
        command = tool(name)
    except RecoveryError:
        return False
    if name == "ykman" and len(command) > 1:  # the bundled copy
        if getattr(sys, "frozen", False):
            return True
        import importlib.util
        return importlib.util.find_spec("ykman") is not None
    return True


def set_workdir(path) -> None:
    global _workdir
    _workdir = Path(path) if path else None


def work_base() -> Path:
    if _workdir is not None:
        return _workdir
    if is_windows():
        root = os.environ.get("LOCALAPPDATA")
        base = Path(root) / "Temp" / "ykrecover" if root else Path(os.environ.get("TEMP", ".")) / "ykrecover"
        base.mkdir(parents=True, exist_ok=True)
        return base
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if runtime and os.path.isdir(runtime) and len(runtime) <= MAX_BASE_LEN:
        return Path(runtime)
    return Path("/tmp")


def memory_backed(path) -> bool:
    if is_windows():
        try:
            root = os.path.splitdrive(str(Path(path).resolve()))[0] + "\\"
            return ctypes.windll.kernel32.GetDriveTypeW(root) == DRIVE_RAMDISK
        except (AttributeError, OSError):
            return False
    best, fstype = "", ""
    try:
        with open("/proc/mounts", encoding="utf-8") as mounts:
            for line in mounts:
                parts = line.split()
                if str(path).startswith(parts[1]) and len(parts[1]) > len(best):
                    best, fstype = parts[1], parts[2]
    except OSError:
        return False
    return fstype in ("tmpfs", "ramfs")


def network_up() -> list:
    """Descriptions of active network connections; empty when the machine looks offline."""
    if is_windows():
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))  # TEST-NET address: a route lookup only, nothing is sent
            address = probe.getsockname()[0]
        except OSError:
            return []
        finally:
            probe.close()
        return [] if address.startswith("127.") or address == "0.0.0.0" else [f"with address {address}"]
    up = []
    for iface in sorted(Path("/sys/class/net").glob("*")):
        try:
            state = (iface / "operstate").read_text().strip()
        except OSError:
            continue
        if iface.name != "lo" and state == "up":
            up.append(iface.name)
    return up


def user_gnupg_home() -> Path:
    if os.environ.get("GNUPGHOME"):
        return Path(os.environ["GNUPGHOME"])
    if is_windows():
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "gnupg"
    return Path.home() / ".gnupg"


def _make_writable_and_retry(func, path, _info) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        func(path)
    except OSError:
        pass


def secure_delete(path) -> None:
    """Remove a temporary home. On Windows (disk-backed), overwrite files with zeros first."""
    path = Path(path)
    if is_windows():
        for item in path.rglob("*"):
            try:
                if item.is_file() and not item.is_symlink():
                    item.chmod(stat.S_IWRITE | stat.S_IREAD)
                    size = item.stat().st_size
                    with open(item, "r+b") as handle:
                        handle.write(b"\0" * size)
                        handle.flush()
                        os.fsync(handle.fileno())
            except OSError:
                pass
    handler = {"onexc": _make_writable_and_retry} if sys.version_info >= (3, 12) \
        else {"onerror": _make_writable_and_retry}
    try:
        shutil.rmtree(path, **handler)
    except OSError:
        pass  # best effort, like the ignore_errors=True it replaces


def run(argv, input=None, env=None) -> subprocess.CompletedProcess:
    """subprocess.run with the tool resolved, UTF-8 text, captured output and no console window."""
    argv = list(argv)
    flags = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if is_windows() else {}
    return subprocess.run(tool(argv[0]) + argv[1:], input=input, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False, **flags)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd recovery && python3 -m unittest tests.test_osdeps -v`
Expected: `OK` (15 tests).

- [ ] **Step 5: Commit**

```bash
git add recovery/ykrecover/osdeps.py recovery/tests/test_osdeps.py
git commit -m "recovery: add osdeps platform layer for Linux and Windows"
```

---

### Task 2: Route the tool through osdeps, add `--workdir` (Python and bash)

**Files:**

- Modify: `recovery/ykrecover/gpg.py`, `recovery/ykrecover/migrate.py`, `recovery/ykrecover/stubs.py`, `recovery/ykrecover/backup.py`, `recovery/ykrecover/cli.py`
- Modify: `recovery/recoverCard.sh`
- Modify: `recovery/tests/fakebin/gpg` (log the GnuPG home that `keytocard` runs in)
- Test: `recovery/tests/test_cli_contract.py`, `recovery/tests/test_fake_hardware.py`, `recovery/tests/test_backup.py`

**Interfaces:**

- Consumes: Task 1 `osdeps.*`.
- Produces:
  - `Options.workdir: Path | None = None`.
  - `Session.validate_workdir()`, called first in `validate_options()` and by the GUI (Task 4). It validates the folder and calls `osdeps.set_workdir`.
  - CLI and bash `--workdir DIR`.
  - `gpg.runtime_base` and `gpg.base_is_tmpfs` are removed, replaced by `osdeps.work_base` and `osdeps.memory_backed`.
  - `migrate.network_interfaces_up` is removed, replaced by `osdeps.network_up`.
  - `stubs.user_home` is removed, replaced by `osdeps.user_gnupg_home`.

- [ ] **Step 1: Write the failing tests**

Add to the `ContractTests` mixin in `recovery/tests/test_cli_contract.py` (it runs for both implementations):

```python
    def test_workdir_must_exist_and_be_short(self):
        self.assertEqual(self.cli("--backup", ED_EXPORT, "--workdir", "/nonexistent/dir", "--dry-run").returncode, 1)
        ok = short_tempdir(self, "wd")
        result = self.cli("--backup", ED_EXPORT, "--workdir", str(ok), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        if sys.platform.startswith("linux"):
            deep = ok / ("d" * 70)
            deep.mkdir()
            self.assertEqual(self.cli("--backup", ED_EXPORT, "--workdir", str(deep), "--dry-run").returncode, 1)
```

(add `import sys` at the top if missing).

Add to the `FakeHardwareTests` mixin in `recovery/tests/test_fake_hardware.py`:

```python
    def test_workdir_hosts_the_temporary_homes(self):
        workdir = short_tempdir(self, "wd")
        result, _, _, state = self.migrate("ed25519", ["--workdir", str(workdir)],
                                           f"{PASSPHRASE}\ny\n\nRESET {S1}\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        homes = (state / "homes.log").read_text().split()
        self.assertTrue(homes)
        self.assertTrue(all(Path(h).resolve().parent == workdir.resolve() for h in homes), homes)
        self.assertEqual(list(workdir.glob("ykr-*")), [])
```

(import `short_tempdir` from `tests.helpers`, and `Path` from `pathlib`).

In `recovery/tests/fakebin/gpg`, inside the `keytocard` branch right after `log(f"keytocard{slot}")`, record the home:

```python
        with open(STATE / "homes.log", "a", encoding="utf-8") as f:
            f.write(os.environ["GNUPGHOME"] + "\n")
```

Add to `recovery/tests/test_backup.py`:

```python
class PreflightTests(unittest.TestCase):
    def test_preflight_reports_missing_gpg4win(self):
        from unittest import mock
        from ykrecover import EXIT_USAGE, osdeps
        from ykrecover.migrate import Options, Session

        class UI:
            def warn(self, msg): pass

        session = Session(Options(backup=Path("."), dry_run=True), UI())
        with mock.patch.object(osdeps, "available", lambda name: name != "gpg"):
            with self.assertRaises(RecoveryError) as ctx:
                session.preflight()
        self.assertEqual(ctx.exception.code, EXIT_USAGE)
        self.assertIn("gpg", str(ctx.exception))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd recovery && python3 -m unittest tests.test_cli_contract tests.test_backup.PreflightTests tests.test_fake_hardware -k workdir -k preflight -v`
Expected: FAIL. `--workdir` is an unknown option (exit 1 for every case), and `homes.log` is missing.

- [ ] **Step 3: Python wiring**

`recovery/ykrecover/gpg.py`:

- Drop `MAX_BASE_LEN`, `runtime_base` and `base_is_tmpfs`, and `import osdeps` (`from . import EXIT_BACKUP, RecoveryError, osdeps`).
- `gpg_version()` uses `osdeps.run(["gpg", "--version"]).stdout`.
- `Home.create` uses `dir=osdeps.work_base()` and the error message `f"cannot create a temporary GnuPG home in {osdeps.work_base()}: {err}"`.
- Replace `Home.run`, `Home.kill_agents` and `Home.destroy` with:

```python
    def run(self, argv, stdin_lines=()) -> subprocess.CompletedProcess:
        data = "".join(f"{line}\n" for line in stdin_lines)
        return osdeps.run(argv, input=data, env=self.env())

    def kill_agents(self) -> None:
        try:
            osdeps.run(["gpgconf", "--kill", "all"], env=self.env())
        except RecoveryError:  # Gpg4win missing: nothing can be running
            pass

    def destroy(self) -> None:
        self.kill_agents()
        osdeps.secure_delete(self.path)
        _LIVE_HOMES.discard(self.path)
```

Keep `import subprocess`, which is still used for the type annotation.

`recovery/ykrecover/migrate.py`:

- Import `osdeps` (`from . import backup, card, modes, osdeps`), and change the gpg import to `from .gpg import Home, gpg_version`.
- Delete `network_interfaces_up`.
- Add `workdir: Path = None` to `Options` after `key`.
- Add the method below and call `self.validate_workdir()` as the first line of `validate_options`:

```python
    def validate_workdir(self) -> None:
        workdir = self.opts.workdir
        if workdir is not None:
            workdir = Path(workdir).resolve()
            if not workdir.is_dir():
                raise RecoveryError(f"--workdir must be an existing folder: {workdir}", EXIT_USAGE)
            if not osdeps.is_windows() and len(str(workdir)) > osdeps.MAX_BASE_LEN:
                raise RecoveryError(f"--workdir path is too long for gpg-agent sockets "
                                    f"(at most {osdeps.MAX_BASE_LEN} characters): {workdir}", EXIT_USAGE)
        osdeps.set_workdir(workdir)
```

- Replace the body of `preflight` with:

```python
    def preflight(self) -> None:
        tools = ["gpg", "gpgconf"] if self.opts.dry_run else ["gpg", "gpgconf", "gpg-connect-agent", "ykman"]
        missing = [t for t in tools if not osdeps.available(t)]
        if missing:
            hint = " (install Gpg4win: https://gpg4win.org)" if osdeps.is_windows() and set(missing) - {"ykman"} else ""
            raise RecoveryError(f"missing tools: {' '.join(missing)}{hint}", EXIT_USAGE)
        if gpg_version() < (2, 2):
            raise RecoveryError("gpg 2.2 or newer is required", EXIT_USAGE)
        if self.opts.dry_run:
            return
        for iface in osdeps.network_up():
            self.ui.warn(f"network interface {iface} is up; an air-gapped system is recommended")
        base = osdeps.work_base()
        if not osdeps.memory_backed(base):
            self.ui.warn(f"temporary key copies are on disk at {base}; "
                         "use --workdir with a RAM disk or encrypted volume to avoid that")
```

- In `write_cards`, replace the `subprocess.run(["gpgconf", "--kill", "scdaemon"], …)` call with `osdeps.run(["gpgconf", "--kill", "scdaemon"])`. In `_list_serials`, replace `subprocess.run(["ykman", "list", "--serials"], …)` with `osdeps.run(["ykman", "list", "--serials"])`.
- Remove `import subprocess` and `import shutil` if they are now unused.
- Any remaining `runtime_base()` or `base_is_tmpfs()` reference becomes `osdeps.work_base()` or `osdeps.memory_backed()`. Check with `grep -n "runtime_base\|base_is_tmpfs\|subprocess" recovery/ykrecover/migrate.py`.

`recovery/ykrecover/stubs.py`:

- Delete `user_home`. Use `home = osdeps.user_gnupg_home()`.
- `missing = [t for t in ("gpg", "gpg-connect-agent") if not osdeps.available(t)]`.
- Each `subprocess.run([...], capture_output=True, text=True, check=False)` becomes `osdeps.run([...])`.
- Import with `from . import …, osdeps` and drop the unused `subprocess` import.

`recovery/ykrecover/backup.py`: in `check_passphrase`, replace `"/dev/null"` with `os.devnull` (add `import os`).

`recovery/ykrecover/cli.py`:

- Add `p.add_argument("--workdir", type=Path)` after `--key`.
- Pass `workdir=args.workdir` into `Options(...)`.
- Leave `update-stubs` unchanged.

- [ ] **Step 4: Bash `--workdir`**

In `recovery/recoverCard.sh`:

- Add `WORKDIR=""` to the defaults line that holds `SOURCE="" KEY=""`.
- In `parse_args`, add `--workdir` to the value-taking option list (`… | --source | --key | --workdir)`) and the case `--workdir) WORKDIR=$2 ;;`.
- In `usage`, change `[--key FPR] [--dry-run] [--yes]` to `[--key FPR] [--workdir DIR] [--dry-run] [--yes]`.
- In `validate_options`, after the three `check_field` lines:

```bash
  if [[ -n $WORKDIR ]]; then
    [[ -d $WORKDIR ]] || die "$EXIT_USAGE" "--workdir must be an existing folder: $WORKDIR"
    WORKDIR=$(realpath -- "$WORKDIR")
    (( ${#WORKDIR} <= 60 )) ||
      die "$EXIT_USAGE" "--workdir path is too long for gpg-agent sockets (at most 60 characters): $WORKDIR"
  fi
```

- Replace `runtime_base` with:

```bash
runtime_base() {
  local base=${XDG_RUNTIME_DIR:-}
  if [[ -n $WORKDIR ]]; then printf '%s' "$WORKDIR"
  elif [[ -n $base && -d $base && ${#base} -le 60 ]]; then printf '%s' "$base"
  else printf '/tmp'; fi
}
```

- In `preflight`, change the tmpfs warning to the shared text:

```bash
  if [[ $(stat -f -c %T "$(runtime_base)") != tmpfs ]]; then
    warn "temporary key copies are on disk at $(runtime_base); use --workdir with a RAM disk or encrypted volume to avoid that"
  fi
```

- [ ] **Step 5: Run the tests**

Run: `cd recovery && python3 -m unittest discover -s tests -t . -v` (about 4 minutes)
Expected: `OK`. The goldens are unchanged: `git diff --stat -- recovery/tests/golden` prints nothing. `shellcheck --severity=error recovery/recoverCard.sh` prints nothing.

- [ ] **Step 6: Commit**

```bash
git add recovery/ykrecover recovery/recoverCard.sh recovery/tests
git commit -m "recovery: route tools and temp homes through osdeps, add --workdir"
```

---

### Task 3: `--ykman` redirect and platform check in the entry point

**Files:**

- Modify: `recovery/ykrecover/__main__.py`
- Test: `recovery/tests/test_entry.py`

**Interfaces:**

- Consumes: `osdeps.supported()`.
- Produces: `python -m ykrecover --ykman ARGS…` (and `ykrecover.exe --ykman …`) runs ykman's CLI with `sys.argv = ["ykman", *ARGS]` before any platform check. Unsupported platforms exit 1 with "ykrecover runs on Linux or Windows only".

- [ ] **Step 1: Update the tests (failing)**

Replace `test_refuses_non_linux_with_a_message` in `recovery/tests/test_entry.py` and add the redirect tests:

```python
    def test_refuses_unsupported_platform_with_a_message(self):
        stderr = io.StringIO()
        with mock.patch.object(entry.sys, "platform", "darwin"), redirect_stderr(stderr):
            self.assertEqual(entry.main(), 1)
        self.assertIn("runs on Linux or Windows only", stderr.getvalue())

    def test_ykman_redirect_hands_over_argv(self):
        seen = {}

        def fake_main():
            seen["argv"] = list(entry.sys.argv)
            return 0

        fake_module = SimpleNamespace(main=fake_main)
        with mock.patch.dict("sys.modules", {"ykman": SimpleNamespace(), "ykman._cli": SimpleNamespace(),
                                             "ykman._cli.__main__": fake_module}), \
                mock.patch.object(entry.sys, "argv", ["ykrecover", "--ykman", "list", "--serials"]), \
                mock.patch.object(entry.sys, "platform", "darwin"):
            self.assertEqual(entry.main(), 0)
        self.assertEqual(seen["argv"], ["ykman", "list", "--serials"])

    @unittest.skipUnless(importlib.util.find_spec("ykman"), "yubikey-manager not installed")
    def test_real_ykman_version(self):
        result = subprocess.run([sys.executable, "-m", "ykrecover", "--ykman", "--version"],
                                capture_output=True, text=True, check=False, cwd=RECOVERY)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("YubiKey Manager", result.stdout)
```

Add imports: `import importlib.util`, `import subprocess`, `import sys`, and `from tests.helpers import RECOVERY`.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd recovery && python3 -m unittest tests.test_entry -v`
Expected: FAIL. The message still says "Linux only", and the redirect returns 1 because the platform check runs first.

- [ ] **Step 3: Implement**

`recovery/ykrecover/__main__.py`:

```python
"""python3 -m ykrecover → GUI; --cli … → terminal; --ykman … → the bundled ykman CLI (Windows build)."""

import sys

UNSUPPORTED = ("ykrecover runs on Linux or Windows only (on Windows, install Gpg4win first). "
               "See recovery/README.md.")


def run_ykman(args: list) -> int:
    """Run ykman's own command line, as if the bundled copy were installed as `ykman`."""
    sys.argv = ["ykman", *args]
    from ykman._cli.__main__ import main as ykman_main
    return ykman_main()


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["--ykman"]:
        return run_ykman(args[1:])
    from . import osdeps
    if not osdeps.supported():
        print(f"error: {UNSUPPORTED}", file=sys.stderr)
        return 1
    if args[:1] == ["--cli"]:
        from .cli import main as cli_main
        return cli_main(args[1:])
    from .gui import main as gui_main
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd recovery && python3 -m unittest tests.test_entry -v`
Expected: `OK`. `test_real_ykman_version` is skipped unless yubikey-manager is installed.

- [ ] **Step 5: Commit**

```bash
git add recovery/ykrecover/__main__.py recovery/tests/test_entry.py
git commit -m "recovery: add --ykman redirect and allow Linux or Windows"
```

---

### Task 4: GUI work folder

**Files:**

- Modify: `recovery/ykrecover/gui.py` (`App.__init__` vars, `build_backup`, `leave_backup`)
- Test: `recovery/tests/test_gui.py`

**Interfaces:**

- Consumes: `Options.workdir`, `Session.validate_workdir()` (Task 2), `osdeps.memory_backed`, `osdeps.work_base`.
- Produces: a "Work folder" row on the Backup page, read before the backup is loaded. The staging home already lives in the chosen folder.

The field goes on the Backup page, not on Card settings, because the staging home is created when the backup loads (`leave_backup`), before Card settings is shown.

- [ ] **Step 1: Write the failing test**

Add to `GuiSmokeTests` in `recovery/tests/test_gui.py`:

```python
    def test_work_folder_is_applied_before_loading(self):
        from pathlib import Path
        from ykrecover import osdeps
        from ykrecover.gui import App
        from tests.helpers import FIXTURES, PASSPHRASE, short_tempdir
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = App(root)
        app.alert = lambda text: None
        self.addCleanup(lambda: app.session and app.session.cleanup())
        self.addCleanup(osdeps.set_workdir, None)
        workdir = short_tempdir(self, "wd")
        app.v["folder"].set(str(FIXTURES / "ed25519" / "export"))
        app.v["workdir"].set(str(workdir))
        app.v["passphrase"].set(PASSPHRASE)
        app.show(1)
        root.update()
        self.assertTrue(app.leave_backup())
        self.assertEqual(Path(app.session.staging.path).parent, workdir)
        self.assertIn(str(workdir), app.workdir_note.cget("text"))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd recovery && python3 -m unittest tests.test_gui -v`
Expected: ERROR, `KeyError: 'workdir'` (or a skip if there is no display; then run it where a display exists).

- [ ] **Step 3: Implement**

In `App.__init__`, add `"workdir": tk.StringVar(),` to `self.v`.

At the end of `build_backup`, append:

```python
        frame = ttk.Frame(self.body)
        frame.pack(fill="x", pady=(8, 0))
        ttk.Label(frame, text="Work folder (optional)", width=26).pack(side="left")
        ttk.Entry(frame, textvariable=self.v["workdir"]).pack(side="left", fill="x", expand=True)
        ttk.Button(frame, text="Browse…", command=lambda: self.v["workdir"].set(
            filedialog.askdirectory(parent=self.root, mustexist=True) or self.v["workdir"].get())).pack(
            side="left", padx=4)
        self.workdir_note = ttk.Label(self.body, wraplength=700, justify="left")
        self.workdir_note.pack(anchor="w")
        self.v["workdir"].trace_add("write", lambda *_: self.update_workdir_note())
        self.update_workdir_note()
```

Add the method below to `App`:

```python
    def update_workdir_note(self):
        text = self.v["workdir"].get().strip()
        if text and not Path(text).is_dir():
            note = "This folder does not exist."
        else:
            base = Path(text) if text else osdeps.work_base()
            note = (f"Temporary key copies go to {base} (memory-backed)." if osdeps.memory_backed(base) else
                    f"Temporary key copies go to {base}, which is on disk. Choose a RAM disk or an "
                    "encrypted volume to avoid that.")
        if self.workdir_note.winfo_exists():
            self.workdir_note.config(text=note)
```

In `leave_backup`, change the line that builds the session to pass the work folder, and validate it before loading:

```python
        workdir = Path(self.v["workdir"].get().strip()) if self.v["workdir"].get().strip() else None
        self.session = Session(Options(backup=candidate.path, source=candidate.kind, workdir=workdir), self.ui)
        try:
            self.session.validate_workdir()
        except RecoveryError as err:
            self.alert(str(err))
            return False
```

Also make the "already loaded" shortcut compare the work folder: `… == (candidate.path, candidate.kind) and s.opts.workdir == workdir`. Move the `workdir = …` line above that check.

Import `osdeps` (`from . import EXIT_CARD, …, osdeps`).

- [ ] **Step 4: Run the tests**

Run: `cd recovery && python3 -m unittest tests.test_gui -v`, then the full suite.
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add recovery/ykrecover/gui.py recovery/tests/test_gui.py
git commit -m "recovery: choose the work folder in the GUI before loading the backup"
```

---

### Task 5: Make the test suite run on Windows

**Files:**

- Modify: `recovery/tests/helpers.py`, `recovery/tests/fakebin/gpg`, `recovery/tests/test_cli_contract.py`, `recovery/tests/test_plan_parity.py`, `recovery/tests/test_fake_hardware.py`, `recovery/tests/test_stubs.py`, `recovery/tests/test_gui.py`
- Create: `.gitattributes`
- Modify: `.gitignore` (whitelist `.gitattributes`; ignore packaging build output)

**Interfaces:**

- Consumes: `osdeps.work_base()`.
- Produces:
  - `helpers._runtime_base()` returns `osdeps.work_base()`.
  - On Windows, `helpers.fake_hardware()` puts `.cmd` shims for the fakes first on PATH, joined with `os.pathsep`.
  - The bash, signal and file-mode tests are skipped off Linux.

- [ ] **Step 1: Line endings**

Create `.gitattributes`:

```gitattributes
# Test fixtures and golden plans are compared byte-for-byte; never convert line endings.
recovery/tests/fixtures/** -text
recovery/tests/golden/** -text
# Scripts run by bash or as Python shebang scripts must keep LF on Windows checkouts.
*.sh text eol=lf
recovery/tests/fakebin/* text eol=lf
```

Append to `.gitignore`:

```gitignore
!.gitattributes
recovery/packaging/build/
recovery/packaging/dist/
```

Run `git add --renormalize .` and confirm `git status --short` lists only `.gitattributes` and `.gitignore`. Every file is already LF.

- [ ] **Step 2: Helpers**

In `recovery/tests/helpers.py`, replace `_runtime_base` with:

```python
def _runtime_base() -> Path:
    from ykrecover import osdeps
    return osdeps.work_base()
```

In `short_tempdir`'s cleanup, make files writable too before `rmtree`:

```python
        for p in [path, *path.rglob("*")]:
            p.chmod(0o700 if p.is_dir() else 0o600)
```

Replace `fake_hardware` with:

```python
def _fake_bin(test) -> Path:
    """The fakes are Python scripts; Windows can only run them through .cmd shims."""
    if os.name != "nt":
        return FAKEBIN
    shims = short_tempdir(test, "bin")
    for name in ("gpg", "ykman", "gpg-connect-agent"):
        (shims / f"{name}.cmd").write_text(f'@"{sys.executable}" "{FAKEBIN / name}" %*\r\n')
    return shims


def fake_hardware(test, serials: list, fail_at: str = "", list_script=None) -> dict:
    """Env overrides that put fake ykman/gpg card operations first on PATH (see fakebin/ykman)."""
    state = short_tempdir(test, "hw")
    (state / "cards").write_text("".join(f"{s}\n" for s in serials))
    if fail_at:
        (state / "fail_at").write_text(fail_at)
    if list_script is not None:
        (state / "list_script").write_text("".join(f"{s}\n" for s in list_script))
    return {
        "PATH": f"{_fake_bin(test)}{os.pathsep}{os.environ['PATH']}",
        "FAKE_HW_DIR": str(state),
        "REAL_GPG": REAL_GPG,
        "FAKE_CERTIFY_PASS": PASSPHRASE,
    }
```

- [ ] **Step 3: Portable fake gpg**

In `recovery/tests/fakebin/gpg`, replace the final `os.execv(REAL, [REAL, *ARGS])` with a pass-through that works on Windows too:

```python
    return subprocess.run([REAL, *ARGS], check=False).returncode
```

- [ ] **Step 4: Skip what cannot run on Windows**

Add `import sys` where needed. Then apply:

```python
LINUX = sys.platform.startswith("linux")
```

- In `test_cli_contract.py`, `test_plan_parity.py`, `test_fake_hardware.py` and `test_stubs.py`, decorate each `Bash…Tests` class with `@unittest.skipUnless(LINUX, "the bash implementation is Linux-only")`.
- In the `FakeHardwareTests` mixin, decorate `test_termination_signal_at_a_prompt_cleans_up` and `test_termination_signal_after_a_written_card_shows_pins` with `@unittest.skipUnless(LINUX, "POSIX signals and select() on pipes")`.
- Decorate `test_outputs_are_owner_only` with `@unittest.skipUnless(LINUX, "POSIX file modes")`.
- In `test_gui.py`, change the class guard to `@unittest.skipIf(tk is None or (sys.platform.startswith("linux") and not os.environ.get("DISPLAY")), "needs tkinter and a display")`.

- [ ] **Step 5: Run the full suite on Linux**

Run: `cd recovery && python3 -m unittest discover -s tests -t . -v`
Expected: `OK`, with the same number of tests run as before this task, and no new skips on Linux. Windows coverage is verified by the CI job in Task 7.

- [ ] **Step 6: Commit**

```bash
git add .gitattributes .gitignore recovery/tests
git commit -m "recovery: let the test suite run on Windows"
```

---

### Task 6: PyInstaller packaging

**Files:**

- Create: `recovery/packaging/requirements-windows.txt`
- Create: `recovery/packaging/entry.py`
- Create: `recovery/packaging/ykrecover.spec`
- Create: `recovery/packaging/build-windows.ps1`
- Create: `recovery/packaging/smoke_test.py`
- Create: `recovery/packaging/README-windows.txt`

**Interfaces:**

- Consumes: `ykrecover.__main__.main` with the `--ykman` redirect (Task 3).
- Produces: `recovery/packaging/dist/ykrecover-windows-x64.zip`, and `smoke_test.py EXE` (exit 0 when the dry-run plan matches the golden plan and `--ykman --version` works).

- [ ] **Step 1: Requirements and entry script**

`recovery/packaging/requirements-windows.txt`:

```text
# Build-time requirements for the Windows executables (pinned; newest releases >= 14 days old on 2026-10-11).
yubikey-manager==5.9.2
pyinstaller==6.22.3
```

`recovery/packaging/entry.py`:

```python
"""PyInstaller entry point for ykrecover.exe and ykrecover-gui.exe."""

import sys

from ykrecover.__main__ import main

sys.exit(main())
```

- [ ] **Step 2: PyInstaller spec**

`recovery/packaging/ykrecover.spec`:

```python
# PyInstaller spec: ykrecover.exe (console) and ykrecover-gui.exe (windowed) from one analysis.
# Build with build-windows.ps1. SPECPATH is defined by PyInstaller.
import os

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

recovery = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[recovery],
    hiddenimports=collect_submodules("ykrecover") + collect_submodules("ykman") + collect_submodules("yubikit"),
    datas=copy_metadata("yubikey-manager"),
    excludes=["pytest"],
)
pyz = PYZ(a.pure)

console = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="ykrecover", console=True, upx=False)
gui = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="ykrecover-gui", console=False, upx=False)
```

- [ ] **Step 3: Build script**

`recovery/packaging/build-windows.ps1`:

```powershell
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
```

Also append `recovery/packaging/.venv/` to the `.gitignore` entries from Task 5. `.*` already hides it, but make it explicit next to `build/` and `dist/`.

- [ ] **Step 4: Smoke test**

`recovery/packaging/smoke_test.py`:

```python
"""CI smoke test for a built ykrecover executable: python smoke_test.py PATH_TO_EXE"""

import subprocess
import sys
from pathlib import Path

RECOVERY = Path(__file__).resolve().parents[1]


def main() -> int:
    exe = sys.argv[1]
    plan = subprocess.run([exe, "--cli", "--backup", str(RECOVERY / "tests" / "fixtures" / "ed25519" / "export"),
                           "--dry-run"], capture_output=True, text=True, check=False)
    if plan.returncode != 0:
        print(f"dry-run failed ({plan.returncode}):\n{plan.stderr}")
        return 1
    golden = (RECOVERY / "tests" / "golden" / "clone-ed25519-export.plan").read_text()
    if plan.stdout != golden:
        print("dry-run plan differs from tests/golden/clone-ed25519-export.plan")
        return 1
    version = subprocess.run([exe, "--ykman", "--version"], capture_output=True, text=True, check=False)
    if version.returncode != 0 or "YubiKey Manager" not in version.stdout:
        print(f"bundled ykman failed ({version.returncode}):\n{version.stdout}\n{version.stderr}")
        return 1
    print(f"smoke test passed: {version.stdout.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: README for the zip**

`recovery/packaging/README-windows.txt`:

```text
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

Before using real backups, run the hardware checklist in recovery/README.md with a spare YubiKey on
this Windows machine.
```

- [ ] **Step 6: Optional local check on Linux**

PyInstaller builds for the platform it runs on. A Linux build still proves the spec, the hidden imports and the `--ykman` redirect:

```bash
python3 -m venv /tmp/ykbuild && /tmp/ykbuild/bin/pip install -q -r recovery/packaging/requirements-windows.txt
cd recovery/packaging && /tmp/ykbuild/bin/python -m PyInstaller --noconfirm --clean --distpath dist --workpath build ykrecover.spec
python3 smoke_test.py dist/ykrecover; cd ../..
```

Expected: `smoke test passed: YubiKey Manager (ykman) version: 5.9.2`. If pip cannot reach PyPI in your environment, skip this step and say so in the report. Delete `recovery/packaging/build` and `recovery/packaging/dist` afterwards; they are git-ignored.

- [ ] **Step 7: Commit**

```bash
git add recovery/packaging .gitignore
git commit -m "recovery: PyInstaller packaging for the Windows executables"
```

---

### Task 7: Windows CI, release workflow and docs

**Files:**

- Modify: `.github/workflows/lint.yml` (add the `windows` job)
- Create: `.github/workflows/release-windows.yml`
- Modify: `recovery/README.md` (Windows section), `CLAUDE.md`

**Interfaces:**

- Consumes: Tasks 5 and 6.
- Produces:
  - a `windows` job on every push and PR (tests, build, smoke test, artifact `ykrecover-windows-x64`);
  - on tags `v*`, the zip attached to the GitHub Release.

- [ ] **Step 1: CI job**

Append to `jobs:` in `.github/workflows/lint.yml`:

```yaml
  windows:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: install Gpg4win
        run: |
          choco install gpg4win -y --no-progress
          foreach ($dir in "${env:ProgramFiles(x86)}\GnuPG\bin", "$env:ProgramFiles\GnuPG\bin") {
            if (Test-Path $dir) { $dir | Out-File -FilePath $env:GITHUB_PATH -Append -Encoding utf8 }
          }

      - name: install build requirements
        run: python -m pip install -r recovery/packaging/requirements-windows.txt

      - name: recovery tests
        working-directory: recovery
        run: python -m unittest discover -s tests -t . -v

      - name: build executables
        run: recovery/packaging/build-windows.ps1

      - name: smoke test
        run: python recovery/packaging/smoke_test.py recovery/packaging/dist/ykrecover.exe

      - uses: actions/upload-artifact@v4
        with:
          name: ykrecover-windows-x64
          path: recovery/packaging/dist/ykrecover-windows-x64.zip
```

- [ ] **Step 2: Release workflow**

`.github/workflows/release-windows.yml`:

```yaml
name: release-windows

on:
  push:
    tags: ["v*"]

permissions:
  contents: write

jobs:
  windows:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: install Gpg4win
        run: |
          choco install gpg4win -y --no-progress
          foreach ($dir in "${env:ProgramFiles(x86)}\GnuPG\bin", "$env:ProgramFiles\GnuPG\bin") {
            if (Test-Path $dir) { $dir | Out-File -FilePath $env:GITHUB_PATH -Append -Encoding utf8 }
          }

      - name: build executables
        run: recovery/packaging/build-windows.ps1

      - name: smoke test
        run: python recovery/packaging/smoke_test.py recovery/packaging/dist/ykrecover.exe

      - name: attach to the release
        env:
          GH_TOKEN: ${{ github.token }}
          TAG: ${{ github.ref_name }}
        run: |
          gh release view $env:TAG *> $null
          if ($LASTEXITCODE -ne 0) {
            gh release create $env:TAG --title $env:TAG --notes "Windows build of the YubiKey recovery tool."
          }
          gh release upload $env:TAG recovery/packaging/dist/ykrecover-windows-x64.zip --clobber
```

The tag reaches the script only through `env`, never interpolated into `run:`, so a crafted tag name cannot inject commands.

- [ ] **Step 3: Docs**

In `recovery/README.md`, replace the paragraph that starts "Both tools run on Linux only" with:

```markdown
The Python tool runs on Linux and Windows; the bash script is Linux-only. On other systems both exit with an error.

**Windows:** install [Gpg4win](https://gpg4win.org), then download `ykrecover-windows-x64.zip` from the repository's Releases page (or the `windows` CI job's artifacts) and run `ykrecover-gui.exe`, or `ykrecover.exe --cli …` in a terminal. ykman is bundled. Windows has no memory-backed temporary folder, so the tool warns that key copies touch the disk while it runs; to avoid that, choose a RAM disk or an encrypted volume as the work folder (`--workdir DIR`, or "Work folder" in the wizard). Run the hardware checklist on Windows too: card access works differently there. To build locally, run `recovery/packaging/build-windows.ps1` on Windows.
```

In the Usage section of `recovery/README.md`, add `--workdir DIR` to the list of options with this line: "`--workdir DIR`: where temporary GnuPG homes go (must exist; on Linux at most 60 characters)".

In `CLAUDE.md`, under "Scripts and Nix", add:

```markdown
- The Python recovery tool also builds for Windows (`recovery/packaging/`, PyInstaller; CI job `windows`, release workflow on `v*` tags). Platform differences live only in `recovery/ykrecover/osdeps.py`; call `osdeps.run()` instead of `subprocess` so tools resolve to Gpg4win/bundled ykman on Windows.
```

- [ ] **Step 4: Verify**

```bash
cd recovery && python3 -m unittest discover -s tests -t . -v && cd ..
shellcheck --severity=error scripts/*.sh piv/*.sh recovery/recoverCard.sh recovery/tests/fixtures/make_fixtures.sh
npx --yes markdownlint-cli2@0.23.3
python3 .github/scripts/check_toc.py README.md
python3 -c "import yaml; [yaml.safe_load(open(f)) for f in ('.github/workflows/lint.yml', '.github/workflows/release-windows.yml')]"
```

Expected: everything passes. If PyYAML is missing, check indentation by eye. The `windows` job itself runs only on GitHub. Report that it has not run yet; it runs when the branch is pushed.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows recovery/README.md CLAUDE.md
git commit -m "recovery: Windows CI job, release workflow and docs"
```
