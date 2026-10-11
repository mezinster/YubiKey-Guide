"""Shared test helpers: fixtures, running either implementation, fake hardware."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RECOVERY = Path(__file__).resolve().parents[1]
FIXTURES = RECOVERY / "tests" / "fixtures"
GOLDEN = RECOVERY / "tests" / "golden"
FAKEBIN = RECOVERY / "tests" / "fakebin"
PASSPHRASE = "test-certify-pass"
REAL_GPG = shutil.which("gpg")


def fixture_info(profile: str) -> dict:
    text = (FIXTURES / profile / "key.txt").read_text()
    return dict(line.split("=", 1) for line in text.split())


def _runtime_base() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR", "")
    if base and os.path.isdir(base) and len(base) <= 60:
        return Path(base)
    return Path("/tmp")


def short_tempdir(test, label: str) -> Path:
    """A temp dir short enough for gpg-agent sockets; agents inside it are killed on cleanup."""
    path = Path(tempfile.mkdtemp(prefix=f"ykt-{label}-", dir=_runtime_base()))

    def cleanup():
        for home in [path, *(p for p in path.rglob("*") if p.is_dir())]:
            subprocess.run(["gpgconf", "--kill", "all"], env={**os.environ, "GNUPGHOME": str(home)},
                           capture_output=True, check=False)
        for p in [path, *path.rglob("*")]:
            if p.is_dir():
                p.chmod(0o700)
        shutil.rmtree(path, ignore_errors=True)

    test.addCleanup(cleanup)
    return path


def run_impl(impl: str, args: list, stdin: str = "", env: dict = None) -> subprocess.CompletedProcess:
    """Run the Python CLI or the bash script with the same arguments."""
    if impl == "python":
        cmd = [sys.executable, "-m", "ykrecover", "--cli", *args]
    else:
        cmd = ["bash", str(RECOVERY / "recoverCard.sh"), *args]
    full_env = {**os.environ, "PYTHONPATH": str(RECOVERY), "LC_ALL": "C", **(env or {})}
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, env=full_env,
                          cwd=RECOVERY, timeout=600, check=False)


def fake_hardware(test, serials: list, fail_at: str = "") -> dict:
    """Env overrides that put fake ykman/gpg card operations first on PATH."""
    state = short_tempdir(test, "hw")
    (state / "cards").write_text("".join(f"{s}\n" for s in serials))
    if fail_at:
        (state / "fail_at").write_text(fail_at)
    return {
        "PATH": f"{FAKEBIN}:{os.environ['PATH']}",
        "FAKE_HW_DIR": str(state),
        "REAL_GPG": REAL_GPG,
        "FAKE_CERTIFY_PASS": PASSPHRASE,
    }
