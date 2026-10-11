"""Throwaway GnuPG homes and subprocess helpers. Secrets only ever travel on stdin."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

MAX_BASE_LEN = 60  # gpg-agent socket paths must stay well under the 108-byte limit


def runtime_base() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR", "")
    if base and os.path.isdir(base) and len(base) <= MAX_BASE_LEN:
        return Path(base)
    return Path("/tmp")


def base_is_tmpfs(base: Path) -> bool:
    best, fstype = "", ""
    try:
        with open("/proc/mounts", encoding="utf-8") as mounts:
            for line in mounts:
                parts = line.split()
                if str(base).startswith(parts[1]) and len(parts[1]) > len(best):
                    best, fstype = parts[1], parts[2]
    except OSError:
        return False
    return fstype in ("tmpfs", "ramfs")


def gpg_version() -> tuple:
    out = subprocess.run(["gpg", "--version"], capture_output=True, text=True, check=False).stdout
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
        path = Path(tempfile.mkdtemp(prefix=f"ykr-{label}-", dir=runtime_base()))
        path.chmod(0o700)
        return cls(path)

    def env(self) -> dict:
        return {**os.environ, "GNUPGHOME": str(self.path)}

    def run(self, argv, stdin_lines=()) -> subprocess.CompletedProcess:
        data = "".join(f"{line}\n" for line in stdin_lines)
        return subprocess.run(list(argv), input=data, env=self.env(), capture_output=True,
                              text=True, check=False)

    def kill_agents(self) -> None:
        subprocess.run(["gpgconf", "--kill", "all"], env=self.env(), capture_output=True, check=False)

    def clone(self, label: str) -> "Home":
        self.kill_agents()
        new = Home.create(label)
        shutil.copytree(self.path, new.path, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("S.*", "*.lock", ".#*", "random_seed"))
        return new

    def destroy(self) -> None:
        self.kill_agents()
        shutil.rmtree(self.path, ignore_errors=True)
