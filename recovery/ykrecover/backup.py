"""Detect backups and load them into a staging home. Sources are only ever read."""

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import EXIT_BACKUP, RecoveryError
from .gpg import Home

KINDS = ("export", "subkeys-only", "gnupghome")
PUBRINGS = ("pubring.kbx", "pubring.gpg")
LIST_SECRET = ("gpg", "--batch", "--with-colons", "--with-keygrip", "--fixed-list-mode", "--list-secret-keys")
HOME_FILES = (*PUBRINGS, "trustdb.gpg")
KEY_PREFIXES = (b"-----BEGIN PGP PRIVATE KEY BLOCK-----", b"-----BEGIN PGP PUBLIC KEY BLOCK-----")
KEY_TAGS = (5, 6, 7, 14)  # secret key, public key, secret subkey, public subkey
MAX_KEY_FILE = 1 << 20
MAX_DEPTH = 3
RANK = {"secret": 2, "secret-unprotected": 1}  # anything else supplies no secret
STATE_WORDS = {"secret": "secret", "secret-unprotected": "secret (no passphrase)", "missing": "missing",
               "card-stub": "card stub", "public": "public"}
PACKET = re.compile(r"^:(secret|public)( sub)? key packet:")


KIND_ORDER = {"gnupghome": 0, "export": 1, "subkeys-only": 2}


@dataclass(frozen=True)
class Candidate:
    kind: str
    path: Path
    files: tuple
    key_id: str = ""  # main-key keyid; empty for a GnuPG home
    unprotected: bool = False  # every secret part it supplies has no passphrase


@dataclass(frozen=True)
class FileReport:
    path: Path
    key: str
    parts: tuple
    status: str  # used | not needed | ignored | unreadable


@dataclass(frozen=True)
class Inventory:
    root: Path
    files: tuple
    candidates: tuple
    unrecoverable: tuple


@dataclass(frozen=True)
class Subkey:
    fpr: str
    keygrip: str
    usage: str
    algo: str
    created: int
    expires: int  # 0 means never
    validity: str
    secret: bool = True  # False when the home holds only a stub (on a card, or missing)


@dataclass(frozen=True)
class KeyBundle:
    key_fp: str
    key_id: str
    uids: tuple
    subkeys: tuple
    has_certify_secret: bool


@dataclass(frozen=True)
class Part:
    main_keyid: str
    keyid: str
    role: str  # main | sub
    state: str  # secret | secret-unprotected | missing | card-stub | public


class AmbiguousKey(RecoveryError):
    def __init__(self, bundles):
        listing = "\n".join(f"  {b.key_fp} {', '.join(b.uids)}" for b in bundles)
        super().__init__(f"several keys found; choose one with --key:\n{listing}", EXIT_BACKUP)
        self.bundles = list(bundles)


def _is_gnupg2_home(folder: Path) -> bool:
    return (folder / "private-keys-v1.d").is_dir() and any((folder / n).is_file() for n in PUBRINGS)


def _home_candidate(folder: Path) -> Candidate:
    names = ("private-keys-v1.d", *HOME_FILES)
    return Candidate("gnupghome", folder, tuple(folder / n for n in names if (folder / n).exists()))


def parse_packets(text: str) -> list:
    """Key parts from `gpg --list-packets` output; the secret material is never decrypted."""
    parts, current, main = [], None, ""

    def close():
        if current and current["keyid"]:
            state = current["state"] or "missing"
            parts.append(Part(current["main"], current["keyid"], current["role"], state))

    for line in text.splitlines():
        if line.startswith(":"):
            close()
            match = PACKET.match(line)
            current = None
            if match:
                secret = match.group(1) == "secret"
                current = {"role": "sub" if match.group(2) else "main", "state": "" if secret else "public",
                           "keyid": "", "main": main}
            continue
        if current is None:
            continue
        text_ = line.strip()
        if "gnu-divert-to-card" in text_:
            current["state"] = "card-stub"
        elif current["state"]:
            pass
        elif "gnu-dummy" in text_:
            current["state"] = "missing"
        elif "S2K" in text_ or (text_.startswith("skey[") and text_.endswith("protected]")):
            current["state"] = "secret"
        elif text_.startswith("skey[") and text_.endswith("bits]"):
            current["state"] = "secret-unprotected"
        if text_.startswith("keyid:"):
            current["keyid"] = text_.split()[1]
            if current["role"] == "main":
                main = current["keyid"]
                current["main"] = main
    close()
    return parts


def looks_like_key_file(path: Path) -> bool:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_KEY_FILE:
            return False
        with path.open("rb") as f:
            head = f.read(64)
    except OSError:
        return False
    if head.removeprefix(b"\xef\xbb\xbf").lstrip().startswith(KEY_PREFIXES):
        return True
    if not head or not head[0] & 0x80:
        return False
    tag = head[0] & 0x3F if head[0] & 0x40 else (head[0] >> 2) & 0x0F
    return tag in KEY_TAGS


def walk(folder: Path) -> tuple:
    """GnuPG 2.x homes and key files under folder (depth 3, byte order, hidden names and random_seed skipped)."""
    homes, files = [], []

    def visit(current: Path, depth: int):
        home = _is_gnupg2_home(current)
        if home:
            homes.append(_home_candidate(current))
        try:
            entries = sorted(current.iterdir(), key=lambda p: os.fsencode(p.name))
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith(".") or entry.name == "random_seed" or entry.is_symlink():
                continue  # random_seed is random bytes that can pass the packet-tag sniff
            if entry.is_dir():
                if entry.name != "private-keys-v1.d" and depth < MAX_DEPTH:
                    visit(entry, depth + 1)
            elif not (home and entry.name in HOME_FILES) and looks_like_key_file(entry):
                files.append(entry)

    visit(folder, 0)
    return homes, files


def classify(path: Path, scan: Home):
    result = scan.run(["gpg", "--batch", "--list-packets", "--", str(path)])
    parts = parse_packets(result.stdout) if result.returncode == 0 else []
    return parts or None


def _select(key: str, rows: list) -> tuple:
    """rows: (walk_index, path, parts) holding this key; returns (used paths, supplied ranks by keyid)."""
    def order(row):
        secret = [p for p in row[2] if RANK.get(p.state, 0)]
        return (-len(secret), -any(p.role == "main" for p in secret), row[0])

    used, supplied = [], {}
    for _, path, parts in sorted(rows, key=order):
        better = [p for p in parts if RANK.get(p.state, 0) > supplied.get(p.keyid, 0)]
        if better:
            used.append(path)
            for p in better:
                supplied[p.keyid] = RANK[p.state]
    return used, supplied


def inventory(folder: Path) -> Inventory:
    if not folder.is_dir():
        raise RecoveryError(f"backup folder not found: {folder}", EXIT_BACKUP)
    homes, paths = walk(folder)
    rows_by_key, order, unreadable = {}, [], []
    if paths:
        scan = Home.create("scan")
        try:
            for index, path in enumerate(paths):
                parts = classify(path, scan)
                if parts is None:
                    unreadable.append(path)
                    continue
                for key in dict.fromkeys(p.main_keyid for p in parts):
                    if key not in rows_by_key:
                        rows_by_key[key] = []
                        order.append(key)
                    rows_by_key[key].append((index, path, tuple(p for p in parts if p.main_keyid == key)))
        finally:
            scan.destroy()
    candidates, reports, unrecoverable, status = list(homes), [], [], {}
    for key in order:
        used, supplied = _select(key, rows_by_key[key])
        for _, path, parts in rows_by_key[key]:
            if path in used:
                status[(path, key)] = "used"
            else:
                status[(path, key)] = "not needed" if any(RANK.get(p.state, 0) for p in parts) else "ignored"
        if not used:
            unrecoverable.append(key)
            continue
        kind = "export" if supplied.get(key, 0) else "subkeys-only"
        candidates.append(Candidate(kind, used[0].parent, tuple(used), key,
                                    all(rank == 1 for rank in supplied.values())))
    for path in paths:
        if path in unreadable:
            reports.append(FileReport(path, "", (), "unreadable"))
        for key in order:
            for _, row_path, parts in rows_by_key[key]:
                if row_path == path:
                    reports.append(FileReport(path, key, parts, status[(path, key)]))
    candidates.sort(key=lambda c: (os.fsencode(str(c.path)), KIND_ORDER[c.kind]))
    return Inventory(folder, tuple(reports), tuple(candidates), tuple(unrecoverable))


def _describe_parts(parts) -> str:
    pieces = [f"main {STATE_WORDS[p.state]}" for p in parts if p.role == "main"]
    for state in STATE_WORDS:
        n = sum(1 for p in parts if p.role == "sub" and p.state == state)
        if n:
            pieces.append(f"{n} subkey{'s' if n > 1 else ''} {STATE_WORDS[state]}")
    return ", ".join(pieces)


def report(inv: Inventory) -> str:
    if not inv.files:
        return ""
    lines = [f"Backup files in {inv.root}:"]
    for f in inv.files:
        rel = f.path.relative_to(inv.root).as_posix()
        if f.status == "unreadable":
            lines.append(f"  {rel}: not a readable key file (ignored)")
        else:
            lines.append(f"  {rel}: key {f.key}: {_describe_parts(f.parts)} ({f.status})")
    lines += [f"  key {k}: not recoverable from this folder (card stubs / public key only)" for k in inv.unrecoverable]
    return "\n".join(lines)


def detect(folder: Path) -> list:
    """Backups in the folder and its subfolders (depth 3), one per recoverable key or GnuPG home."""
    return list(inventory(folder).candidates)


def _listing(candidates) -> str:
    return "\n".join(f"  {c.kind}: {c.path}" + (f" (key {c.key_id})" if c.key_id else "") for c in candidates)


def choose(candidates, kind=None, key=None) -> Candidate:
    found = list(candidates)
    if kind:
        candidates = [c for c in candidates if c.kind == kind]
    if key:
        want = key.upper().replace(" ", "")
        candidates = [c for c in candidates if not c.key_id or want.endswith(c.key_id) or c.key_id.endswith(want)]
    if len(candidates) == 1:
        return candidates[0]
    if not found:
        raise RecoveryError("no backup found (expected secret-*-Certify.key, secret-*-Subkeys.key "
                            "or a GnuPG home)", EXIT_BACKUP)
    if not candidates:
        raise RecoveryError(f"no backup matches --source or --key; found:\n{_listing(found)}", EXIT_BACKUP)
    raise RecoveryError(f"several backups found; narrow --backup or pass --source or --key:\n{_listing(candidates)}",
                        EXIT_BACKUP)


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
                   "created": int(f[5] or 0), "expires": int(f[6] or 0), "validity": f[1], "secret": f[14] == "+"}
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
            try:
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                    for p in [dst, *dst.rglob("*")]:
                        p.chmod(0o700 if p.is_dir() else 0o600)
                else:
                    shutil.copy2(src, dst)
                    dst.chmod(0o600)
            except (OSError, shutil.Error) as err:
                raise RecoveryError(f"cannot copy {src} into staging: {err}", EXIT_BACKUP) from err
    else:
        for src in candidate.files:
            result = staging.run(["gpg", "--batch", "--import", "--", str(src)])
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
                          "--armor", "--yes", "--output", os.devnull, what, bundle.key_fp], [passphrase])
    staging.kill_agents()  # drop the cached passphrase so later scripted prompts are predictable
    return result.returncode == 0
