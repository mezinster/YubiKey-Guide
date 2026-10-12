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


def bare_and_protected_copies(test) -> tuple:
    """The unprotected fixture key exported (binary) twice: with no passphrase, then protected by PASSPHRASE."""
    info, home = fixture_info("unprotected"), short_tempdir(test, "rk")
    env = {**os.environ, "GNUPGHOME": str(home)}

    def gpg(*args, stdin=""):
        return subprocess.run([REAL_GPG, "--batch", "--pinentry-mode", "loopback", *args], input=stdin.encode(),
                              env=env, check=True, capture_output=True).stdout

    gpg("--import", str(FIXTURES / "unprotected" / "key.asc"))
    bare = gpg("--export-secret-keys", info["KEY_FP"])
    gpg("--passphrase-fd", "0", "--passwd", info["KEY_FP"], stdin=PASSPHRASE + "\n")
    protected = gpg("--passphrase-fd", "0", "--export-secret-keys", info["KEY_FP"], stdin=PASSPHRASE + "\n")
    return bare, protected


def repeated_key_folder(test) -> Path:
    """A folder with one file holding the fixture key twice: with no passphrase first, then protected.

    gpg imports the first copy and leaves the second "unchanged"."""
    bare, protected = bare_and_protected_copies(test)
    root = short_tempdir(test, "rep")
    (root / "both.gpg").write_bytes(bare + protected)
    return root


def bare_first_folder(test) -> Path:
    """Two full copies of the fixture key: a-bare.gpg (no passphrase, first in walk order), b-protected.gpg."""
    bare, protected = bare_and_protected_copies(test)
    root = short_tempdir(test, "bf")
    (root / "a-bare.gpg").write_bytes(bare)
    (root / "b-protected.gpg").write_bytes(protected)
    return root


def bom_crlf_folder(test) -> Path:
    """The unprotected fixture key as armor with a UTF-8 BOM and CRLF line ends, which gpg cannot parse as is."""
    root = short_tempdir(test, "bom")
    text = (FIXTURES / "unprotected" / "key.asc").read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    (root / "bom.asc").write_bytes(b"\xef\xbb\xbf" + text)
    return root


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
    """Env overrides that put fake ykman/gpg card operations first on PATH (see fakebin/ykman).

    On Windows the fakes are .cmd shims, which osdeps refuses unless YKRECOVER_TEST_ALLOW_SCRIPTS=1."""
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
        "YKRECOVER_TEST_ALLOW_SCRIPTS": "1",  # let osdeps run the .cmd shims on Windows (test-only)
    }
