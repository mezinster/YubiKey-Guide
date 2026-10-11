"""Everything that differs between Linux and Windows. Platform checks read sys.platform per call."""

import ctypes
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
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


def _which(name: str):
    """Windows PATH lookup that never considers the current directory (binary planting)."""
    extensions = [e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e]
    candidates = []
    for ext in extensions:
        candidates += [name + ext, name + ext.lower()]
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry or entry == "." or not os.path.isabs(entry):
            continue
        for candidate in candidates:
            full = Path(entry) / candidate
            if full.is_file():
                return str(full)
    return None


def tool(name: str) -> list:
    """The command line that runs the tool a plan calls `name`."""
    if not is_windows():
        return [name]
    found = _which(name)
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
    raise RecoveryError(f"{name} not found", EXIT_USAGE)


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
    return _workdir if _workdir is not None else default_work_base()


def default_work_base() -> Path:
    """The base used when no work folder was chosen (ignores any set_workdir override)."""
    if is_windows():
        root = os.environ.get("LOCALAPPDATA")
        base = Path(root) / "Temp" / "ykrecover" if root else Path(tempfile.gettempdir()) / "ykrecover"
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
                here, mount = str(path), parts[1]
                if (here == mount or here.startswith(mount.rstrip("/") + "/")) and len(mount) > len(best):
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


def secure_delete(path) -> bool:
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
    return not path.exists()


def run(argv, input=None, env=None) -> subprocess.CompletedProcess:
    """Run a tool: resolved, UTF-8 bytes in (no CRLF translation), str out, captured, no console window."""
    argv = list(argv)
    flags = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if is_windows() else {}
    if input is None:
        flags["stdin"] = subprocess.DEVNULL
    data = input.encode("utf-8") if input is not None else None
    done = subprocess.run(tool(argv[0]) + argv[1:], input=data, env=env, capture_output=True,
                          check=False, **flags)

    def text(raw):
        return (raw or b"").decode("utf-8", "replace").replace("\r\n", "\n")

    return subprocess.CompletedProcess(done.args, done.returncode, text(done.stdout), text(done.stderr))
