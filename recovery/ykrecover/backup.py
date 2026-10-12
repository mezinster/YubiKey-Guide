"""Detect backups and load them into a staging home. Sources are only ever read."""

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import EXIT_BACKUP, RecoveryError
from .gpg import Home, gpg_version

KINDS = ("export", "subkeys-only", "gnupghome")
PUBRINGS = ("pubring.kbx", "pubring.gpg")
LIST_SECRET = ("gpg", "--batch", "--with-colons", "--with-keygrip", "--fixed-list-mode", "--list-secret-keys")
HOME_FILES = (*PUBRINGS, "trustdb.gpg")
BOM = b"\xef\xbb\xbf"
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
    bare: tuple = ()  # keyids of the parts it supplies with no passphrase


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
    expires: int = 0  # main key; 0 means never
    algo: str = ""


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
    if head.removeprefix(BOM).lstrip().startswith(KEY_PREFIXES):
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


def readable_copy(path: Path, home: Home, name: str):
    """gpg cannot parse armor that starts with a UTF-8 BOM: such a file is read from a BOM-stripped copy in home.

    None when the file or the copy cannot be read or written."""
    try:
        with path.open("rb") as f:
            if f.read(len(BOM)) != BOM:
                return path
            data = f.read()
        copy = home.path / name
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(copy, flags, 0o600), "wb") as f:
            f.write(data)
    except OSError:
        return None
    return copy


def classify(path: Path, scan: Home):
    source = readable_copy(path, scan, "bom.key")
    if source is None:
        return None
    result = scan.run(["gpg", "--batch", "--list-packets", "--", str(source)])
    parts = parse_packets(result.stdout) if result.returncode == 0 else []
    return parts or None


def _select(key: str, rows: list) -> tuple:
    """rows: (walk_index, path, parts) holding this key; returns (used paths, supplied ranks by keyid).

    gpg keeps the first copy of a key it imports and reports later copies "unchanged", so files holding
    protected copies are imported first, and a keyid's first secret copy decides its state."""
    def order(row):
        ranks = [RANK.get(p.state, 0) for p in row[2]]
        secret = [p for p in row[2] if RANK.get(p.state, 0)]
        return (-max(ranks, default=0), -len(secret), -any(p.role == "main" for p in secret), row[0])

    used, supplied = [], {}
    for _, path, parts in sorted(rows, key=order):
        new = [p for p in parts if RANK.get(p.state, 0) and p.keyid not in supplied]
        if new:
            used.append(path)
            for p in new:
                supplied.setdefault(p.keyid, RANK[p.state])
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
        bare = tuple(keyid for keyid, rank in supplied.items() if rank == 1)
        candidates.append(Candidate(kind, used[0].parent, tuple(used), key, len(bare) == len(supplied), bare))
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
    """The candidate --source and --key pick; callers report a folder with no candidates before this."""
    found = list(candidates)
    if kind:
        candidates = [c for c in candidates if c.kind == kind]
    if key:
        want = key.upper().replace(" ", "")
        candidates = [c for c in candidates if not c.key_id or want.endswith(c.key_id) or c.key_id.endswith(want)]
    if len(candidates) == 1:
        return candidates[0]
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
            current = {"key_fp": "", "key_id": f[4], "uids": [], "subkeys": [], "has_certify_secret": f[14] != "#",
                       "expires": int(f[6] or 0), "algo": f[16] or f"rsa{f[2]}"}
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
    return [KeyBundle(b["key_fp"], b["key_id"], tuple(b["uids"]), tuple(b["subkeys"]), b["has_certify_secret"],
                      b["expires"], b["algo"]) for b in bundles]


def list_bundles(home: Home) -> list:
    return parse_secret_keys(home.run(LIST_SECRET).stdout)


# GnuPG 2.5 imports an ECDH secret subkey that has no passphrase through build_mode1003_sexp (g10/import.c), which
# passes pkey[2], the KDF parameters, where the public point q (pkey[1]) belongs; GnuPG master passes pkey[1].
# gpg-agent then files the key under the keygrip of that wrong q: one fixed name per curve and KDF, so a second
# such subkey with the same curve and KDF finds the name taken and is dropped ("secret key already exists").
# gpg lists a misfiled or dropped subkey as having no secret. d is intact, so the repair below puts the real q
# back and files the key under its real keygrip, then proves the pair with a decryption. GnuPG 2.4 and a fixed
# GnuPG never misfile a key: there the repair finds nothing to do.
GNUPG25_DROPPED = ("GnuPG 2.5 drops additional unprotected ECDH subkeys on import ({}); use GnuPG 2.4, or add a "
                   "passphrase to the backup's keys first (gpg --passwd)")
CHECK_TEXT = "ykrecover ECDH check"


@dataclass(frozen=True)
class EcdhSubkey:
    keyid: str
    fpr: str
    grip: str
    curve: str  # dotted OID, as build_mode1003_sexp writes it
    q: str
    kdf: str
    flag: str  # colon field 15: + secret, # none
    validity: str
    created: int


def oid_text(hexstr: str) -> str:
    """Dotted form of a --with-key-data curve OID (a length byte, then the DER content)."""
    try:
        raw = bytes.fromhex(hexstr)
    except ValueError:
        return ""
    if not raw or raw[0] != len(raw) - 1 or len(raw) < 2:
        return ""
    arcs, value = [min(raw[1] // 40, 2), raw[1] - 40 * min(raw[1] // 40, 2)], 0
    for byte in raw[2:]:
        value = value << 7 | byte & 0x7F
        if not byte & 0x80:
            arcs.append(value)
            value = 0
    return ".".join(map(str, arcs))


def ecdh_subkeys(colons: str) -> list:
    """The ECDH subkeys in a --with-keygrip --with-key-data listing."""
    found, sub = [], None
    for line in colons.splitlines():
        f = line.split(":")
        f += [""] * (20 - len(f))
        if f[0] in ("sec", "ssb", "pub", "sub"):
            sub = None
            if f[0] in ("ssb", "sub") and f[3] == "18":
                sub = {"keyid": f[4], "flag": f[14], "validity": f[1], "created": int(f[5] or 0), "fpr": "",
                       "grip": "", "pkd": {}}
                found.append(sub)
        elif sub is not None and f[0] == "fpr" and not sub["fpr"]:
            sub["fpr"] = f[9]
        elif sub is not None and f[0] == "grp" and not sub["grip"]:
            sub["grip"] = f[9]
        elif sub is not None and f[0] == "pkd":
            sub["pkd"].setdefault(f[1], f[3])
    return [EcdhSubkey(s["keyid"], s["fpr"], s["grip"], oid_text(s["pkd"].get("0", "")), s["pkd"].get("1", ""),
                       s["pkd"].get("2", ""), s["flag"], s["validity"], s["created"])
            for s in found if s["fpr"] and s["grip"] and s["pkd"].get("1") and s["pkd"].get("2")]


def _misfiled_as(data: bytes, sub: EcdhSubkey) -> bool:
    """A key file that build_mode1003_sexp wrote for this subkey: its curve, and its KDF parameters as q."""
    flat = re.sub(rb"\s+", b"", data)
    return (sub.curve != "" and f'(curve"{sub.curve}")'.encode() in flat
            and re.search(rb"\(q#" + re.escape(sub.kdf.encode()) + rb"#\)", flat, re.IGNORECASE) is not None)


def _bindings(home: Home) -> dict:
    """Newest subkey binding signature time by subkey fingerprint."""
    newest, current = {}, ""
    for line in home.run(["gpg", "--batch", "--with-colons", "--fixed-list-mode", "--list-sigs"]).stdout.splitlines():
        f = line.split(":")
        f += [""] * (20 - len(f))
        if f[0] in ("pub", "sub", "uid"):
            current = "" if f[0] != "sub" else "next"
        elif f[0] == "fpr" and current == "next":
            current = f[9]
        elif f[0] == "sig" and current and current != "next" and f[10].startswith("18"):
            newest[current] = max(newest.get(current, 0), int(f[5] or 0))
    return newest


def decrypts(home: Home, sub: EcdhSubkey, bindings: dict) -> bool:
    """Encrypts to the subkey alone and decrypts with it. An expired subkey is encrypted to at the time of its
    newest binding signature."""
    message = home.path / "ecdh-check.asc"
    for when in (None, max(sub.created, bindings.get(sub.fpr, 0)) + 1):
        faked = ["--faked-system-time", f"{when}!"] if when else []
        made = home.run(["gpg", "--batch", *faked, "--trust-model", "always", "--armor", "--yes", "--output",
                         str(message), "--recipient", sub.fpr + "!", "--encrypt"], [CHECK_TEXT])
        if made.returncode == 0:
            break
    else:
        return False
    try:
        plain = home.run(["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase-fd", "0", "--decrypt",
                          "--", str(message)], [""])
    finally:
        message.unlink(missing_ok=True)
    return plain.returncode == 0 and plain.stdout == CHECK_TEXT + "\n"


def repair_ecdh_imports(home: Home, bare=()) -> None:
    """Undoes GnuPG 2.5's misfiling of imported ECDH subkeys that have no passphrase (see GNUPG25_DROPPED).

    Repaired: subkeys gpg lists with no secret, and those the backup supplies with no passphrase (bare keyids,
    as the inventory chose; the misfiled copy no longer blocked a later protected copy, and GnuPG 2.4 keeps the
    first). A misfiled file is claimed only by the one subkey of its curve and KDF that needs it; two claimants
    mean GnuPG dropped one of them. Revoked subkeys never go to a card and are left alone."""
    def ecdh():
        listing = home.run([*LIST_SECRET[:-1], "--with-key-data", LIST_SECRET[-1]]).stdout
        return [s for s in ecdh_subkeys(listing) if s.validity != "r"]

    wanted = [s for s in ecdh() if s.flag == "#" or (s.flag == "+" and s.keyid in bare)]
    keys = home.path / "private-keys-v1.d"
    try:
        files = {p: p.read_bytes() for p in sorted(keys.glob("*.key"))} if wanted else {}
    except OSError as err:
        raise RecoveryError(f"cannot read the imported keys: {err}", EXIT_BACKUP) from err
    claims = {}
    for sub in wanted:
        matches = [p for p, data in files.items() if _misfiled_as(data, sub)]
        if len(matches) > 1:
            raise RecoveryError(f"several key files could be subkey {sub.fpr} misfiled by GnuPG 2.5: "
                                f"{' '.join(p.name for p in matches)}", EXIT_BACKUP)
        if matches:
            claims.setdefault(matches[0], []).append(sub)
    refiled = []
    for path, subs in claims.items():
        if len(subs) > 1:
            raise RecoveryError(GNUPG25_DROPPED.format(" ".join(s.fpr for s in subs)), EXIT_BACKUP)
        sub = subs[0]
        bad_q = re.compile(rb"\(q\s*#\s*" + rb"\s*".join(re.escape(c.encode()) for c in sub.kdf) + rb"\s*#\s*\)",
                           re.IGNORECASE)
        try:
            temp = keys / f"{sub.grip}.tmp"
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
            with os.fdopen(os.open(temp, flags, 0o600), "wb") as f:
                f.write(bad_q.sub(b"(q #" + sub.q.encode() + b"#)", files[path], count=1))
            os.replace(temp, keys / f"{sub.grip}.key")
            path.unlink()
        except OSError as err:
            raise RecoveryError(f"cannot repair the imported subkey {sub.fpr}: {err}", EXIT_BACKUP) from err
        refiled.append(sub)
    bindings = _bindings(home) if refiled else {}
    for sub in refiled:
        if not decrypts(home, sub, bindings):
            raise RecoveryError(f"subkey {sub.fpr} does not decrypt after undoing GnuPG 2.5's misfiling of it; "
                                "use GnuPG 2.4, or add a passphrase to the backup's keys first (gpg --passwd)",
                                EXIT_BACKUP)
    if bare and wanted:
        dropped = [s.fpr for s in ecdh() if s.flag == "#" and s.keyid in bare]
        if dropped and gpg_version() == (2, 5):
            raise RecoveryError(GNUPG25_DROPPED.format(" ".join(dropped)), EXIT_BACKUP)


# gpg exits non-zero when it skips any part of a file it imports (GnuPG 2.5 also for a user ID with no valid
# self-signature), yet it may have imported the secret keys. A backup file counts as imported when gpg's IMPORT_RES
# status line says it imported secret keys or already had them; whether those are the chosen key's, with its secret
# parts, is decided after the import, from the staged keys (list_bundles, choose_key, Session.validate_bundle).
SKIP_KEY = re.compile(r"^key [0-9A-F]{8,40}: ")
SKIP_DONE = re.compile(r'^key [0-9A-F]{8,40}: (public key ".*" imported|secret key imported|".*)$')
SKIP_NOISE = re.compile(r"^(keybox '.*' created|.*: trustdb created| *[A-Za-z /-]+: *[0-9]+)$")


def secret_keys_imported(status: str) -> bool:
    """True when an IMPORT_RES status line counts secret keys imported or unchanged (gpg's sec_imported and
    sec_dups fields)."""
    lines = [line.split() for line in status.splitlines() if line.startswith("[GNUPG:] IMPORT_RES ")]
    fields = lines[-1] if lines else []
    if len(fields) < 14 or not all(re.fullmatch(r"[0-9]+", f) for f in fields[12:14]):
        return False
    return int(fields[12]) + int(fields[13]) > 0


def skipped_line(stderr: str, code: int) -> str:
    """The first gpg message about a key it did not take, else its first message that is not routine."""
    lines = [line[5:] for line in stderr.splitlines() if line.startswith("gpg: ")]
    keys = [line for line in lines if SKIP_KEY.match(line) and not SKIP_DONE.match(line)]
    other = [line for line in lines if not SKIP_DONE.match(line) and not SKIP_NOISE.match(line)]
    return (keys or other or [f"gpg exited with status {code}"])[0]


def load(candidate: Candidate, staging: Home, warn=None) -> list:
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
        for index, src in enumerate(candidate.files):
            source = readable_copy(src, staging, f"bom-{index}.key")
            if source is None:
                raise RecoveryError(f"cannot import {src}: cannot read the file", EXIT_BACKUP)
            result = staging.run(["gpg", "--batch", "--status-fd", "1", "--import", "--", str(source)])
            if result.returncode != 0:
                if not secret_keys_imported(result.stdout):
                    raise RecoveryError(f"cannot import {src}: {result.stderr.strip()}", EXIT_BACKUP)
                if warn:
                    warn(f"gpg skipped part of {src}: {skipped_line(result.stderr, result.returncode)}")
        repair_ecdh_imports(staging, candidate.bare)
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
