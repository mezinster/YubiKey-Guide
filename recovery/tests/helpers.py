"""Shared test helpers: fixtures, running either implementation, fake hardware."""

import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ykrecover import RecoveryError, osdeps

RECOVERY = Path(__file__).resolve().parents[1]
FIXTURES = RECOVERY / "tests" / "fixtures"
GOLDEN = RECOVERY / "tests" / "golden"
FAKEBIN = RECOVERY / "tests" / "fakebin"
PASSPHRASE = "test-certify-pass"
LINUX = sys.platform.startswith("linux")
REAL_GPG = osdeps.tool("gpg")[0] if osdeps.is_windows() else shutil.which("gpg")


def fixture_info(profile: str) -> dict:
    text = (FIXTURES / profile / "key.txt").read_text()
    return dict(line.split("=", 1) for line in text.split())


def _runtime_base() -> Path:
    return osdeps.default_work_base()


def short_tempdir(test, label: str) -> Path:
    """A temp dir short enough for gpg-agent sockets; agents inside it are killed on cleanup."""
    path = Path(tempfile.mkdtemp(prefix=f"ykt-{label}-", dir=_runtime_base()))

    def is_gnupg_home(p):
        return (p / "pubring.kbx").exists() or (p / "private-keys-v1.d").exists() or any(p.glob("S.gpg-agent*"))

    def cleanup():
        for home in [path, *(p for p in path.rglob("*") if p.is_dir())]:
            if is_gnupg_home(home):
                try:
                    osdeps.run(["gpgconf", "--kill", "all"], env={**os.environ, "GNUPGHOME": str(home)})
                except RecoveryError:
                    pass
        for p in [path, *path.rglob("*")]:
            try:
                p.chmod(0o700 if p.is_dir() else 0o600)
            except OSError:
                pass
        osdeps.secure_delete(path)

    test.addCleanup(cleanup)
    return path


def _command(impl: str, args: list, env: dict) -> tuple:
    if impl == "python":
        cmd = [sys.executable, "-m", "ykrecover", "--cli", *args]
    else:
        cmd = ["bash", str(RECOVERY / "recoverCard.sh"), *args]
    return cmd, {**os.environ, "PYTHONPATH": str(RECOVERY), "LC_ALL": "C", **(env or {})}


def run_impl(impl: str, args: list, stdin: str = "", env: dict = None) -> subprocess.CompletedProcess:
    """Run the Python CLI or the bash script with the same arguments."""
    cmd, full_env = _command(impl, args, env)
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, env=full_env,
                          cwd=RECOVERY, timeout=600, check=False)


def spawn_impl(test, impl: str, args: list, env: dict = None) -> subprocess.Popen:
    """Start either implementation with stdin and stderr as pipes (binary); killed on cleanup."""
    cmd, full_env = _command(impl, args, env)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            env=full_env, cwd=RECOVERY)

    def cleanup():
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdin.close()
        proc.stderr.close()

    test.addCleanup(cleanup)
    return proc


def read_until(stream, text: str, timeout: float) -> str:
    """Read a pipe until text appears (prompts end without a newline); fails after timeout seconds."""
    seen, deadline = b"", time.monotonic() + timeout
    while text.encode() not in seen:
        left = deadline - time.monotonic()
        if left <= 0 or not select.select([stream], [], [], left)[0]:
            raise AssertionError(f"timed out waiting for {text!r}; got:\n{seen.decode(errors='replace')}")
        chunk = os.read(stream.fileno(), 4096)
        if not chunk:
            raise AssertionError(f"stream closed before {text!r}; got:\n{seen.decode(errors='replace')}")
        seen += chunk
    return seen.decode(errors="replace")


def _fake_bin(test) -> Path:
    """The fakes are Python scripts; Windows can only run them through .cmd shims."""
    if os.name != "nt":
        return FAKEBIN
    shims = short_tempdir(test, "bin")
    for name in ("gpg", "ykman", "gpg-connect-agent"):
        (shims / f"{name}.cmd").write_bytes(f'@"{sys.executable}" "{FAKEBIN / name}" %*\r\n'.encode())
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
