"""Detect backups and load them into a staging home. Sources are only ever read."""

import shutil
from dataclasses import dataclass
from pathlib import Path

from . import EXIT_BACKUP, RecoveryError
from .gpg import Home

KINDS = ("export", "subkeys-only", "gnupghome")
PUBRINGS = ("pubring.kbx", "pubring.gpg")
LIST_SECRET = ("gpg", "--batch", "--with-colons", "--with-keygrip", "--fixed-list-mode", "--list-secret-keys")


@dataclass(frozen=True)
class Candidate:
    kind: str
    path: Path
    files: tuple


@dataclass(frozen=True)
class Subkey:
    fpr: str
    keygrip: str
    usage: str
    algo: str
    created: int
    expires: int  # 0 means never
    validity: str


@dataclass(frozen=True)
class KeyBundle:
    key_fp: str
    key_id: str
    uids: tuple
    subkeys: tuple
    has_certify_secret: bool


class AmbiguousKey(RecoveryError):
    def __init__(self, bundles):
        listing = "\n".join(f"  {b.key_fp} {', '.join(b.uids)}" for b in bundles)
        super().__init__(f"several keys found; choose one with --key:\n{listing}", EXIT_BACKUP)
        self.bundles = list(bundles)


def _detect_dir(folder: Path) -> list:
    found = []
    if (folder / "private-keys-v1.d").is_dir() and any((folder / n).is_file() for n in PUBRINGS):
        names = ("private-keys-v1.d", *PUBRINGS, "trustdb.gpg")
        found.append(Candidate("gnupghome", folder, tuple(folder / n for n in names if (folder / n).exists())))
    certify = tuple(sorted(folder.glob("secret-*-Certify.key")))
    subkeys = tuple(sorted(folder.glob("secret-*-Subkeys.key")))
    if certify:
        found.append(Candidate("export", folder, certify))
    elif subkeys:
        found.append(Candidate("subkeys-only", folder, subkeys))
    return found


def detect(folder: Path) -> list:
    """Backups in the folder itself and in its immediate subfolders."""
    if not folder.is_dir():
        raise RecoveryError(f"backup folder not found: {folder}", EXIT_BACKUP)
    found = _detect_dir(folder)
    for sub in sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith(".")):
        if sub.name != "private-keys-v1.d":
            found.extend(_detect_dir(sub))
    return found


def choose(candidates, kind=None) -> Candidate:
    if kind:
        candidates = [c for c in candidates if c.kind == kind]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise RecoveryError("no backup found (expected secret-*-Certify.key, secret-*-Subkeys.key "
                            "or a GnuPG home)", EXIT_BACKUP)
    listing = "\n".join(f"  {c.kind}: {c.path}" for c in candidates)
    raise RecoveryError(f"several backups found; narrow --backup or pass --source:\n{listing}", EXIT_BACKUP)


def parse_secret_keys(colons: str) -> list:
    bundles, current, sub = [], None, None

    def close_sub():
        nonlocal sub
        if sub is not None:
            current["subkeys"].append(Subkey(**sub))
        sub = None

    for line in colons.splitlines():
        f = line.split(":")
        f += [""] * (20 - len(f))
        if f[0] == "sec":
            if current is not None:
                close_sub()
            current = {"key_fp": "", "key_id": f[4], "uids": [], "subkeys": [], "has_certify_secret": f[14] != "#"}
            bundles.append(current)
        elif f[0] == "ssb" and current is not None:
            close_sub()
            sub = {"fpr": "", "keygrip": "", "usage": f[11], "algo": f[16] or f"rsa{f[2]}",
                   "created": int(f[5] or 0), "expires": int(f[6] or 0), "validity": f[1]}
        elif f[0] == "fpr" and current is not None:
            if sub is not None and not sub["fpr"]:
                sub["fpr"] = f[9]
            elif not current["key_fp"]:
                current["key_fp"] = f[9]
        elif f[0] == "grp" and sub is not None:
            sub["keygrip"] = f[9]
        elif f[0] == "uid" and current is not None:
            current["uids"].append(f[9])
    if current is not None:
        close_sub()
    return [KeyBundle(b["key_fp"], b["key_id"], tuple(b["uids"]), tuple(b["subkeys"]), b["has_certify_secret"])
            for b in bundles]


def list_bundles(home: Home) -> list:
    return parse_secret_keys(home.run(LIST_SECRET).stdout)


def load(candidate: Candidate, staging: Home) -> list:
    if candidate.kind == "gnupghome":
        staging.kill_agents()
        for src in candidate.files:  # read-only media keeps its mode bits; make the copies usable
            dst = staging.path / src.name
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
                for p in [dst, *dst.rglob("*")]:
                    p.chmod(0o700 if p.is_dir() else 0o600)
            else:
                shutil.copy2(src, dst)
                dst.chmod(0o600)
    else:
        for src in candidate.files:
            result = staging.run(["gpg", "--batch", "--import", str(src)])
            if result.returncode != 0:
                raise RecoveryError(f"cannot import {src}: {result.stderr.strip()}", EXIT_BACKUP)
    bundles = list_bundles(staging)
    if not bundles:
        raise RecoveryError(f"no secret keys in {candidate.path}", EXIT_BACKUP)
    return bundles


def choose_key(bundles, key_fp=None) -> KeyBundle:
    if key_fp:
        want = key_fp.upper().replace(" ", "")
        matches = [b for b in bundles if b.key_fp.endswith(want)]
        if not matches:
            raise RecoveryError(f"no key matches --key {key_fp}", EXIT_BACKUP)
    else:
        matches = list(bundles)
    if len(matches) == 1:
        return matches[0]
    raise AmbiguousKey(matches)


def check_passphrase(staging: Home, bundle: KeyBundle, passphrase: str) -> bool:
    what = "--export-secret-keys" if bundle.has_certify_secret else "--export-secret-subkeys"
    result = staging.run(["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase-fd", "0",
                          "--armor", "--yes", "--output", "/dev/null", what, bundle.key_fp], [passphrase])
    staging.kill_agents()  # drop the cached passphrase so later scripted prompts are predictable
    return result.returncode == 0
