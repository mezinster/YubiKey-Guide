"""Throwaway GnuPG homes and subprocess helpers. Secrets only ever travel on stdin."""

import atexit
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import EXIT_BACKUP, RecoveryError, osdeps

_LIVE_HOMES = set()  # every home created and not yet destroyed; removed at exit if still there


def gpg_version() -> tuple:
    out = osdeps.run(["gpg", "--version"]).stdout
    try:
        major, minor = out.splitlines()[0].split()[-1].split(".")[:2]
        return int(major), int(minor)
    except (IndexError, ValueError):
        return (0, 0)


class Home:
    """A GNUPGHOME owned by this tool. Every gpg call names its home explicitly."""

    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def create(cls, label: str) -> "Home":
        path = None
        try:
            path = Path(tempfile.mkdtemp(prefix=f"ykr-{label}-", dir=osdeps.work_base()))
            _LIVE_HOMES.add(path)
            path.chmod(0o700)
        except OSError as err:
            if path:
                shutil.rmtree(path, ignore_errors=True)
                _LIVE_HOMES.discard(path)
            raise RecoveryError(f"cannot create a temporary GnuPG home in {osdeps.work_base()}: {err}",
                                EXIT_BACKUP) from err
        return cls(path)

    def env(self) -> dict:
        return {**os.environ, "GNUPGHOME": str(self.path)}

    def run(self, argv, stdin_lines=()) -> subprocess.CompletedProcess:
        data = "".join(f"{line}\n" for line in stdin_lines)
        return osdeps.run(argv, input=data, env=self.env())

    def kill_agents(self) -> None:
        try:
            osdeps.run(["gpgconf", "--kill", "all"], env=self.env())
        except RecoveryError:  # Gpg4win missing: nothing can be running
            pass

    def clone(self, label: str) -> "Home":
        self.kill_agents()
        new = Home.create(label)
        try:
            shutil.copytree(self.path, new.path, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("S.*", "*.lock", ".#*", "random_seed"))
        except OSError:
            new.destroy()
            raise
        return new

    def destroy(self) -> bool:
        """True when the folder is gone. A folder that survives stays registered for the exit hook."""
        self.kill_agents()
        gone = osdeps.secure_delete(self.path)
        if gone:
            _LIVE_HOMES.discard(self.path)
        return gone


@atexit.register
def destroy_all_homes() -> None:
    """Last line of defence when a run ends without its own cleanup (for example a signal mid-cleanup)."""
    for path in list(_LIVE_HOMES):
        Home(path).destroy()
