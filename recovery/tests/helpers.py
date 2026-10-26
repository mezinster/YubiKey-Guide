"""Shared test helpers: fixtures, running either implementation, fake hardware."""

import base64
import hashlib
import importlib.util
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ykrecover import RecoveryError, osdeps
from ykrecover.backup import repair_ecdh_imports
from ykrecover.gpg import Home

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


def import_unprotected(home: Path) -> None:
    """Imports the unprotected fixture key into home as the tool does (GnuPG 2.5 needs the ECDH repair)."""
    subprocess.run([REAL_GPG, "--batch", "--import", str(FIXTURES / "unprotected" / "key.asc")],
                   env={**os.environ, "GNUPGHOME": str(home)}, check=True, capture_output=True)
    repair_ecdh_imports(Home(home))


def bare_and_protected_copies(test) -> tuple:
    """The unprotected fixture key exported (binary) twice: with no passphrase, then protected by PASSPHRASE."""
    info, home = fixture_info("unprotected"), short_tempdir(test, "rk")
    env = {**os.environ, "GNUPGHOME": str(home)}

    def gpg(*args, stdin=""):
        return subprocess.run([REAL_GPG, "--batch", "--pinentry-mode", "loopback", *args], input=stdin.encode(),
                              env=env, check=True, capture_output=True).stdout

    import_unprotected(home)
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


def _packets():
    spec = importlib.util.spec_from_file_location("make_stubs", FIXTURES / "make_stubs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dearmor(text: bytes) -> bytes:
    lines = text.decode("ascii").replace("\r", "").split("\n")
    body = lines[lines.index("") + 1:]
    return base64.b64decode("".join(line for line in body if line and not line.startswith(("=", "-----"))))


def without_uid_self_sigs(data: bytes, drop_uids: bool = False) -> bytes:
    """A binary key export with the signatures after each user ID dropped (gpg finds no valid user ID in it), or
    with the user IDs dropped too (gpg finds none at all)."""
    packets, out, in_uid = _packets(), b"", False
    for tag, body in packets.read_packets(data):
        in_uid = tag == 13 or (in_uid and tag == 2)
        if not (in_uid and (tag == 2 or drop_uids)):
            out += packets.write_packet(tag, body)
    return out


MALLORY = "Mallory Müller <m@example.org>"
REVOKED_UID = "Revoked Test <r@example.org>"


def revoked_uid_folder(test) -> Path:
    """The unprotected fixture key with a second user ID that is revoked, exported (binary) into a folder."""
    info, home, root = fixture_info("unprotected"), short_tempdir(test, "rv"), short_tempdir(test, "rev")
    env = {**os.environ, "GNUPGHOME": str(home)}

    def gpg(*args):
        return subprocess.run([REAL_GPG, "--batch", "--pinentry-mode", "loopback", "--passphrase", "", *args],
                              env=env, check=True, capture_output=True).stdout

    import_unprotected(home)
    gpg("--quick-add-uid", info["KEY_FP"], REVOKED_UID)
    gpg("--quick-revoke-uid", info["KEY_FP"], REVOKED_UID)
    (root / "key.gpg").write_bytes(gpg("--export-secret-keys", info["KEY_FP"]))
    return root


def partial_import_folder(test, shape: str) -> Path:
    """A backup file with a broken key block, made from the fixture keys at test time (partial.gpg).

    Plain `gpg --import` (no --allow-non-selfsigned-uid) of each:
    user:       the ed25519 key's secret block with no user ID self-signature, then its public block. GnuPG 2.5
                skips the user ID ("no valid user IDs"), exits non-zero and imports the main key's secret but
                none of the subkeys'; GnuPG 2.4 stops at the secret block and imports nothing secret.
    bad-public: the same key's public block with no user ID self-signature, then its secret block. GnuPG 2.5
                exits non-zero and imports every secret; GnuPG 2.4 does the same and exits 0.
    unsigned:   only the ed25519 key's secret block with no user ID self-signature: nothing secret comes in.
    two-keys:   the ed25519 key's secret block, then the unprotected key's secret block with no user ID at all.
                Every GnuPG exits non-zero ("no user ID") and imports the ed25519 secret key only, with or
                without --allow-non-selfsigned-uid.
    forged:     the ed25519 key's secret block with a second user ID (MALLORY, non-ASCII) that has no
                signature: GnuPG skips that user ID ("no valid user IDs" on 2.5 if it were the only one).
    rejected:   only the unprotected key's secret block with no user ID at all: nothing secret comes in, with
                or without --allow-non-selfsigned-uid."""
    home, root = short_tempdir(test, "pi"), short_tempdir(test, f"pi-{shape}")
    env = {**os.environ, "GNUPGHOME": str(home)}

    def gpg(*args):
        return subprocess.run([REAL_GPG, "--batch", *args], env=env, check=True, capture_output=True).stdout

    import_unprotected(home)
    unprotected = gpg("--export-secret-keys", fixture_info("unprotected")["KEY_FP"])
    no_uid = without_uid_self_sigs(unprotected, drop_uids=True)
    export = FIXTURES / "ed25519" / "export"
    ed25519 = dearmor((export / f"secret-{fixture_info('ed25519')['KEY_ID']}-Certify.key").read_bytes())
    public = dearmor(next(export.glob("public-*.asc")).read_bytes())
    forged, added = b"", False
    for tag, body in _packets().read_packets(ed25519):
        if tag == 7 and not added:  # an unsigned user ID after the signed one
            forged += _packets().write_packet(13, MALLORY.encode())
            added = True
        forged += _packets().write_packet(tag, body)
    data = {"user": without_uid_self_sigs(ed25519) + public, "bad-public": without_uid_self_sigs(public) + ed25519,
            "forged": forged,
            "unsigned": without_uid_self_sigs(ed25519), "two-keys": ed25519 + no_uid, "rejected": no_uid}[shape]
    (root / "partial.gpg").write_bytes(data)
    return root


def loose_subkey_folder(test, armored: bool = False, public: bool = True, stubs: bool = False) -> Path:
    """The rsa4096 fixture key as a backup that never held its main key's secret: public.asc (the public key, as
    exported) and sk_<KEYID>.gpg, the encryption subkey's secret saved as a key of its own (a secret-key packet
    with no user ID, signature or subkey). The file is armored with armored, and public.asc is left out when not
    public. With stubs, a-stub.asc (first in walk order) holds the whole key as card stubs."""
    info, packets, root = fixture_info("rsa4096"), _packets(), short_tempdir(test, "loose")
    export = FIXTURES / "rsa4096" / "export"
    subkeys = dearmor((export / f"secret-{info['KEY_ID']}-Subkeys.key").read_bytes())
    for tag, body in packets.read_packets(subkeys):
        if tag != 7:
            continue
        public_part = body[:packets.public_end(body)]
        if hashlib.sha1(b"\x99" + len(public_part).to_bytes(2, "big") + public_part).hexdigest().upper() \
                == info["FPR_E"]:
            loose = packets.write_packet(5, body)
            break
    else:
        raise AssertionError("the fixture has no encryption subkey secret")
    name = f"sk_{info['FPR_E'][-16:]}.gpg"
    (root / name).write_bytes(packets.armor(loose).encode() if armored else loose)
    if public:
        shutil.copy(next(export.glob("public-*.asc")), root / "public.asc")
    if stubs:
        whole = dearmor((export / f"secret-{info['KEY_ID']}-Certify.key").read_bytes())
        carded = b"".join(packets.write_packet(tag, packets.stub(body, "card") if tag in (5, 7) else body)
                          for tag, body in packets.read_packets(whole))
        (root / "a-stub.asc").write_text(packets.armor(carded))
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
