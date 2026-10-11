# YubiKey Recovery Toolset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a toolset that migrates GnuPG key backups (created per this guide before `keytocard`) onto new YubiKeys. It has a Python GUI/CLI and an equally complete bash script.

**Architecture:** A stdlib-only Python package (`recovery/ykrecover/`) builds a canonical list of steps (each one a command, its stdin lines with secret placeholders, and an optional check) and then either prints it (`--dry-run`) or executes it in throwaway GnuPG homes. `recovery/recoverCard.sh` builds the identical step list in bash. Both are driven by the same `unittest` classes, which compare dry-run plans byte-for-byte against golden files and run full migrations against fake `ykman`/`gpg` card operations.

**Tech Stack:** Python ≥ 3.11 (stdlib only: `tkinter`, `unittest`, `subprocess`), bash ≥ 4.4, GnuPG ≥ 2.2, ykman, Nix (LiveCD), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-11-yubikey-recovery-design.md` (including "Decisions made during planning")

## Global Constraints

- Python ≥ 3.11, standard library only. No pip installs at runtime or for tests.
- Bash ≥ 4.4. Header comment plus `set -o errtrace -o nounset -o pipefail -o errexit`, like `scripts/*.sh`. `export LC_ALL=C`.
- External tools: `gpg` ≥ 2.2, `gpgconf`, `gpg-connect-agent`, `ykman`. Linux only.
- Exit codes: `0` all cards OK · `1` usage/preflight · `2` backup/passphrase · `3` a card failed or was skipped · `130` interrupted/aborted.
- Secrets (Certify passphrase, PINs) reach gpg only on stdin. They are never put in env vars or logs, and never printed except the final one-time PIN display. The single exception is ykman's `-a <ADMIN_PIN>`, a documented spec decision.
- Every scripted gpg session uses `--batch` and `--pinentry-mode loopback`. Every scripted gpg card/edit session and every `ykman` call is immediately preceded by a `gpgconf --kill all` step in the same home.
- Temporary homes: `ykr-<label>-*` under `$XDG_RUNTIME_DIR` if that path is ≤ 60 characters, else `/tmp`. Mode 0700. They are always deleted (with agents killed) on exit.
- The backup folder is only ever read. `--out` must be outside it and must not already contain `secret-*.key`.
- Dry-run plan format (byte-identical across implementations):
  `STEP <n> <phase> <home> <argv joined by single spaces>`, then `STDIN <line>` per stdin line, then `CHECK <name>` if any.
- Card metadata defaults: name blank, login `yk.` + 16 random `[a-z0-9]` per card, URL blank.
- Run tests from `recovery/`: `python3 -m unittest discover -s tests -t . -v`

## Review Focus

1. **Real card prompts differ from the scripted stdin.** A gpg or firmware version may ask one more or one fewer question. Expected: the step fails with gpg's message and Retry/Skip/Abort is offered; a cached prompt can never shift a PIN into the wrong answer. Pinned by Task 3 `test_every_card_session_starts_after_kill`.
2. **No card detected, or the card is held by another daemon.** Expected: a clear "expected exactly one YubiKey, found 0" message and a re-prompt, not a crash; EOF aborts with 130. Pinned by Task 6 `test_no_card_reprompts_then_eof_aborts` (both implementations).
3. **Backup on read-only media** (LUKS volume mounted read-only). Expected: loading and dry-run succeed, because nothing writes into the backup. Pinned by Task 4 `test_read_only_backup` (both implementations).
4. **Output folder already holding an earlier new backup.** Expected: refuse with exit 1 rather than overwrite. Pinned by Task 4 `test_out_with_existing_keys_rejected` (both implementations).
5. **Abort mid-run leaves secret-bearing temp homes.** Expected: every `ykr-*` home created by the run is gone after it exits. Pinned by Task 6 `test_abort_cleans_up_homes` (both implementations).

---

### Task 1: Package skeleton, test fixtures and test helpers

**Files:**

- Create: `recovery/ykrecover/__init__.py`
- Create: `recovery/tests/__init__.py` (empty)
- Create: `recovery/tests/fixtures/make_fixtures.sh`
- Create (generated): `recovery/tests/fixtures/{ed25519,rsa4096,expired}/...`
- Create: `recovery/tests/helpers.py`
- Test: `recovery/tests/test_fixtures.py`

**Interfaces:**

- Produces: `ykrecover.EXIT_OK/EXIT_USAGE/EXIT_BACKUP/EXIT_CARD/EXIT_INTERRUPT`, `RecoveryError(message, code)` with `.code`, `Aborted(Exception)`.
- Produces (tests): `helpers.RECOVERY, FIXTURES, GOLDEN, FAKEBIN, PASSPHRASE, REAL_GPG`, `fixture_info(profile) -> dict` with keys `KEY_FP, KEY_ID, FPR_S, FPR_E, FPR_A`, `short_tempdir(test, label) -> Path`, `run_impl(impl, args, stdin="", env=None) -> CompletedProcess`, `fake_hardware(test, serials, fail_at="") -> dict` (env overrides).
- Fixture layout per profile: `<p>/export/secret-<KID>-Certify.key`, `<p>/export/secret-<KID>-Subkeys.key`, `<p>/export/public-<KID>-2026-10-11.asc`, `<p>/subkeys-only/secret-<KID>-Subkeys.key`, `<p>/gnupghome/{private-keys-v1.d/,pubring.kbx,trustdb.gpg}`, `<p>/key.txt`. Passphrase: `test-certify-pass`.

- [ ] **Step 1: Write the package init**

`recovery/ykrecover/__init__.py`:

```python
"""Migrate GnuPG key backups onto new YubiKeys. See recovery/README.md."""

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_BACKUP = 2
EXIT_CARD = 3
EXIT_INTERRUPT = 130


class RecoveryError(Exception):
    """A user-facing failure that ends the run with a specific exit code."""

    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


class Aborted(Exception):
    """The user cancelled, or stdin reached EOF at a prompt."""
```

- [ ] **Step 2: Write the fixture generator**

`recovery/tests/fixtures/make_fixtures.sh`:

```bash
#!/usr/bin/env bash
# Regenerates the throwaway test keys in this folder. Outputs are committed;
# rerun only when the fixture layout changes. Passphrase: test-certify-pass

set -o errtrace -o nounset -o pipefail -o errexit
export LC_ALL=C

cd "$(dirname "$0")"
PASS=test-certify-pass

make_profile() { # make_profile NAME PRIMARY_ALGO "SIGN ENCRYPT AUTH" EXPIRY [FAKED_TIME]
  local name=$1 primary=$2 expiry=$4 faked=${5:-} home fp kid usage
  local -a algos time_opt=()
  read -r -a algos <<<"$3"
  [[ -z $faked ]] || time_opt=(--faked-system-time "$faked")
  home=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/ykfix-XXXXXX")
  export GNUPGHOME=$home
  rm -rf "$name"
  mkdir -p "$name/export" "$name/subkeys-only" "$name/gnupghome"
  printf '%s\n' "$PASS" | gpg --batch "${time_opt[@]}" --pinentry-mode loopback \
    --passphrase-fd 0 --quick-generate-key "Recovery Test ($name) <test@example.org>" "$primary" cert never
  fp=$(gpg --with-colons --list-keys | awk -F: '/^fpr/ {print $10; exit}')
  kid=${fp: -16}
  for usage in sign encrypt auth; do
    printf '%s\n' "$PASS" | gpg --batch "${time_opt[@]}" --pinentry-mode loopback \
      --passphrase-fd 0 --quick-add-key "$fp" "${algos[0]}" "$usage" "$expiry"
    algos=("${algos[@]:1}")
  done
  printf '%s\n' "$PASS" | gpg --batch --pinentry-mode loopback --passphrase-fd 0 --armor \
    --output "$name/export/secret-$kid-Certify.key" --export-secret-keys "$fp"
  printf '%s\n' "$PASS" | gpg --batch --pinentry-mode loopback --passphrase-fd 0 --armor \
    --output "$name/export/secret-$kid-Subkeys.key" --export-secret-subkeys "$fp"
  gpg --batch --armor --output "$name/export/public-$kid-2026-10-11.asc" --export "$fp"
  cp "$name/export/secret-$kid-Subkeys.key" "$name/subkeys-only/"
  gpgconf --kill all
  cp -a "$home/private-keys-v1.d" "$home/pubring.kbx" "$home/trustdb.gpg" "$name/gnupghome/"
  rm -f "$name"/gnupghome/private-keys-v1.d/*.lock
  {
    printf 'KEY_FP=%s\nKEY_ID=%s\n' "$fp" "$kid"
    gpg --with-colons --list-keys "$fp" |
      awk -F: '$1 == "sub" {u = $12} $1 == "fpr" && u {print "FPR_" toupper(u) "=" $10; u = ""}'
  } >"$name/key.txt"
  rm -rf "$home"
}

make_profile ed25519 ed25519 "ed25519 cv25519 ed25519" 2099-12-31
make_profile rsa4096 rsa4096 "rsa4096 rsa4096 rsa4096" 2099-12-31
make_profile expired ed25519 "ed25519 cv25519 ed25519" 1y 20200101T000000
```

- [ ] **Step 3: Generate the fixtures**

Run: `chmod +x recovery/tests/fixtures/make_fixtures.sh && recovery/tests/fixtures/make_fixtures.sh`
Expected: exits 0 (RSA generation can take a minute). `cat recovery/tests/fixtures/ed25519/key.txt` shows 5 lines: `KEY_FP=…`, `KEY_ID=…`, `FPR_S=…`, `FPR_E=…`, `FPR_A=…`.

- [ ] **Step 4: Write the test helpers**

`recovery/tests/helpers.py`:

```python
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
```

- [ ] **Step 5: Write the fixture sanity test**

`recovery/tests/test_fixtures.py`:

```python
import unittest

from tests.helpers import FIXTURES, fixture_info


class FixtureTests(unittest.TestCase):
    def test_profiles_have_every_backup_format(self):
        for profile in ("ed25519", "rsa4096", "expired"):
            with self.subTest(profile):
                info = fixture_info(profile)
                self.assertEqual(set(info), {"KEY_FP", "KEY_ID", "FPR_S", "FPR_E", "FPR_A"})
                kid = info["KEY_ID"]
                root = FIXTURES / profile
                self.assertTrue((root / "export" / f"secret-{kid}-Certify.key").is_file())
                self.assertTrue((root / "export" / f"secret-{kid}-Subkeys.key").is_file())
                self.assertTrue((root / "subkeys-only" / f"secret-{kid}-Subkeys.key").is_file())
                self.assertTrue((root / "gnupghome" / "private-keys-v1.d").is_dir())
                self.assertTrue((root / "gnupghome" / "pubring.kbx").is_file())
                self.assertFalse(list((root / "subkeys-only").glob("*Certify*")))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: Run the test**

Run: `cd recovery && python3 -m unittest tests.test_fixtures -v`
Expected: `OK` (1 test).

- [ ] **Step 7: Commit**

```bash
git add recovery/ykrecover/__init__.py recovery/tests
git commit -m "add recovery package skeleton, test fixtures and helpers"
```

---

### Task 2: GnuPG homes and backup loading

**Files:**

- Create: `recovery/ykrecover/gpg.py`
- Create: `recovery/ykrecover/backup.py`
- Test: `recovery/tests/test_backup.py`

**Interfaces:**

- Consumes: `RecoveryError`, `EXIT_BACKUP` (Task 1).
- Produces (`gpg.py`): `runtime_base() -> Path`, `base_is_tmpfs(base: Path) -> bool`, `gpg_version() -> tuple[int, int]`, `class Home(path)` with `Home.create(label) -> Home`, `.env() -> dict`, `.run(argv, stdin_lines=()) -> CompletedProcess` (text, captured, never raises), `.kill_agents()`, `.clone(label) -> Home`, `.destroy()`.
- Produces (`backup.py`): `KINDS`, `Candidate(kind, path, files)`, `Subkey(fpr, keygrip, usage, algo, created, expires, validity)` (`expires` 0 = never, `usage` like `"s"`), `KeyBundle(key_fp, key_id, uids, subkeys, has_certify_secret)`, `AmbiguousKey(RecoveryError)` with `.bundles`, `detect(folder) -> list[Candidate]`, `choose(candidates, kind=None) -> Candidate`, `load(candidate, staging: Home) -> list[KeyBundle]`, `list_bundles(home) -> list[KeyBundle]`, `parse_secret_keys(colons: str) -> list[KeyBundle]`, `choose_key(bundles, key_fp=None) -> KeyBundle`, `check_passphrase(staging, bundle, passphrase) -> bool`.

- [ ] **Step 1: Write the failing tests**

`recovery/tests/test_backup.py`:

```python
import unittest
from pathlib import Path

from tests.helpers import FIXTURES, PASSPHRASE, fixture_info
from ykrecover import EXIT_BACKUP, RecoveryError, backup
from ykrecover.gpg import Home

COLONS_TWO_KEYS = """\
sec:u:255:22:D35238B02045AC9D:1791717022:::u:::cESCA:::+::ed25519:::0:
fpr:::::::::9D1EEF5129527741D5D71C30D35238B02045AC9D:
grp:::::::::1BD940563F0ED08451AB0FA1929B36AD4BE43F7C:
uid:u::::1791717022::230162C5BA27CB835FEFA182F7362D77F77BB1A7::Test <t@example.org>::::::::::0:
ssb:u:255:22:A3C895A8B9C0084C:1791717024:1854789024:::::s:::+::ed25519::
fpr:::::::::289425A50E5E40018F9BBD22A3C895A8B9C0084C:
grp:::::::::EEA8DE772E836F74E1BB4AC797F79DB808D4A3BD:
ssb:u:4096:1:266DF3F59D5DF0EF:1791717025::::::e:::#::::
fpr:::::::::B3B43A994B5B2F01BF225D2A266DF3F59D5DF0EF:
grp:::::::::3E79726AA188E01C2C4436695167BF9F1F9917D0:
sec:u:255:22:1111111111111111:1791717022:::u:::cESCA:::#::ed25519:::0:
fpr:::::::::AAAA1111111111111111111111111111111111111:
"""


class ParseTests(unittest.TestCase):
    def test_parses_keys_and_subkeys(self):
        first, second = backup.parse_secret_keys(COLONS_TWO_KEYS)
        self.assertEqual(first.key_fp, "9D1EEF5129527741D5D71C30D35238B02045AC9D")
        self.assertEqual(first.key_id, "D35238B02045AC9D")
        self.assertEqual(first.uids, ("Test <t@example.org>",))
        self.assertTrue(first.has_certify_secret)
        sign, enc = first.subkeys
        self.assertEqual((sign.usage, sign.algo, sign.expires), ("s", "ed25519", 1854789024))
        self.assertEqual(sign.keygrip, "EEA8DE772E836F74E1BB4AC797F79DB808D4A3BD")
        self.assertEqual((enc.usage, enc.algo, enc.expires), ("e", "rsa4096", 0))
        self.assertFalse(second.has_certify_secret)

    def test_choose_key(self):
        bundles = backup.parse_secret_keys(COLONS_TWO_KEYS)
        with self.assertRaises(backup.AmbiguousKey) as ctx:
            backup.choose_key(bundles)
        self.assertEqual(ctx.exception.code, EXIT_BACKUP)
        self.assertEqual(len(ctx.exception.bundles), 2)
        self.assertEqual(backup.choose_key(bundles, "02045ac9d").key_id, "D35238B02045AC9D")
        with self.assertRaises(RecoveryError):
            backup.choose_key(bundles, "FFFF")


class DetectTests(unittest.TestCase):
    def test_detects_each_format(self):
        for kind in ("export", "subkeys-only", "gnupghome"):
            with self.subTest(kind):
                found = backup.detect(FIXTURES / "ed25519" / kind)
                self.assertEqual([c.kind for c in found], [kind])

    def test_parent_folder_needs_a_choice(self):
        found = backup.detect(FIXTURES / "ed25519")
        self.assertEqual([c.kind for c in found], ["export", "gnupghome", "subkeys-only"])
        with self.assertRaises(RecoveryError) as ctx:
            backup.choose(found)
        self.assertEqual(ctx.exception.code, EXIT_BACKUP)
        self.assertEqual(backup.choose(found, "gnupghome").kind, "gnupghome")

    def test_missing_folder(self):
        with self.assertRaises(RecoveryError) as ctx:
            backup.detect(Path("/nonexistent/backup"))
        self.assertEqual(ctx.exception.code, EXIT_BACKUP)


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.home = Home.create("test")
        self.addCleanup(self.home.destroy)

    def load(self, profile: str, kind: str):
        candidate = backup.choose(backup.detect(FIXTURES / profile / kind))
        return backup.choose_key(backup.load(candidate, self.home))

    def test_export_matches_fixture(self):
        info = fixture_info("ed25519")
        bundle = self.load("ed25519", "export")
        self.assertEqual((bundle.key_fp, bundle.key_id), (info["KEY_FP"], info["KEY_ID"]))
        self.assertTrue(bundle.has_certify_secret)
        self.assertEqual({k.usage: k.fpr for k in bundle.subkeys},
                         {"s": info["FPR_S"], "e": info["FPR_E"], "a": info["FPR_A"]})
        self.assertEqual({k.algo for k in bundle.subkeys}, {"ed25519", "cv25519"})

    def test_rsa_algorithm_names(self):
        self.assertEqual({k.algo for k in self.load("rsa4096", "export").subkeys}, {"rsa4096"})

    def test_subkeys_only_has_no_certify_secret(self):
        self.assertFalse(self.load("ed25519", "subkeys-only").has_certify_secret)

    def test_gnupghome_copy(self):
        self.assertEqual(self.load("ed25519", "gnupghome").key_fp, fixture_info("ed25519")["KEY_FP"])

    def test_passphrase_check(self):
        bundle = self.load("ed25519", "export")
        self.assertFalse(backup.check_passphrase(self.home, bundle, "wrong"))
        self.assertTrue(backup.check_passphrase(self.home, bundle, PASSPHRASE))

    def test_source_is_not_modified(self):
        source = FIXTURES / "ed25519" / "gnupghome"
        before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in source.rglob("*")}
        self.load("ed25519", "gnupghome")
        after = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in source.rglob("*")}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd recovery && python3 -m unittest tests.test_backup -v`
Expected: ERROR, `ModuleNotFoundError: No module named 'ykrecover.gpg'`.

- [ ] **Step 3: Implement `gpg.py`**

`recovery/ykrecover/gpg.py`:

```python
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
```

- [ ] **Step 4: Implement `backup.py`**

`recovery/ykrecover/backup.py`:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd recovery && python3 -m unittest tests.test_backup -v`
Expected: `OK` (11 tests).

- [ ] **Step 6: Commit**

```bash
git add recovery/ykrecover/gpg.py recovery/ykrecover/backup.py recovery/tests/test_backup.py
git commit -m "recovery: detect and load key backups into staging homes"
```

---

### Task 3: Canonical step builders (plan, modes, card)

**Files:**

- Create: `recovery/ykrecover/plan.py`
- Create: `recovery/ykrecover/modes.py`
- Create: `recovery/ykrecover/card.py`
- Test: `recovery/tests/test_steps.py`

**Interfaces:**

- Consumes: `KeyBundle`, `Subkey` (Task 2), `RecoveryError`, `EXIT_BACKUP`.
- Produces (`plan.py`): `STAGING="<STAGING>"`, `CARDHOME="<CARDHOME>"`, `VERIFYHOME="<VERIFYHOME>"`, `SECRET_TOKENS`, `Step(phase, home, argv, stdin=(), check="")`, `format_plan(steps) -> str`, `substitute(text, tokens) -> str`, `public_tokens(tokens) -> dict`.
- Produces (`modes.py`): `USAGES=("s","e","a")`, `USAGE_WORD`, `ALGOS`, `ROTATED`, `select_targets(bundle) -> {"s": fpr, "e": fpr, "a": fpr}`, `expired(bundle, targets, now) -> list[fpr]`, `default_algo(bundle, targets) -> "rsa4096"|"ed25519"`, `mode_steps(mode, bundle, targets, expire, algo, revoke_old) -> list[Step]`, `backup_steps(mode, bundle) -> list[Step]`, `finish_steps(key_fp, key_id, targets) -> list[Step]`.
- Produces (`card.py`): `DEFAULT_ADMIN_PIN`, `DEFAULT_USER_PIN`, `SLOTS`, `TOUCH_POLICIES`, `CardSettings(retries, touch, name, url)`, `generate_pins() -> (admin, user)`, `random_login() -> str`, `split_name(name) -> (surname, given)`, `card_steps(index, key_fp, targets, settings) -> list[Step]`, `verify_steps(index, key_fp, targets) -> list[Step]`, `parse_serials(text) -> list[str]`, `parse_card_fprs(colons) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

`recovery/tests/test_steps.py`:

```python
import unittest

from ykrecover import RecoveryError, card, modes
from ykrecover.backup import KeyBundle, Subkey
from ykrecover.plan import Step, format_plan, public_tokens, substitute

FP = "A" * 40


def sub(fpr, usage, created, algo="ed25519", expires=0, validity="u"):
    return Subkey(fpr, "G" + fpr[1:], usage, algo, created, expires, validity)


BUNDLE = KeyBundle(FP, "A" * 16, ("T <t@x>",), (
    sub("S1" * 20, "s", 100), sub("E1" * 20, "e", 100, "cv25519"), sub("A1" * 20, "a", 100),
    sub("S2" * 20, "s", 200), sub("S3" * 20, "s", 300, validity="r"),
), True)


class PlanTests(unittest.TestCase):
    def test_format_plan(self):
        steps = [Step("mode", "<STAGING>", ("gpg", "--x", "y"), ("<CERTIFY_PASS>", "")),
                 Step("verify1", "<VERIFYHOME>", ("gpg", "--card-status"), check="cardstatus")]
        self.assertEqual(format_plan(steps),
                         "STEP 1 mode <STAGING> gpg --x y\nSTDIN <CERTIFY_PASS>\nSTDIN \n"
                         "STEP 2 verify1 <VERIFYHOME> gpg --card-status\nCHECK cardstatus\n")

    def test_substitute_and_redact(self):
        tokens = {"<ADMIN_PIN>": "87654321", "<OUT>": "/out"}
        self.assertEqual(substitute("-a <ADMIN_PIN> <OUT>/x", tokens), "-a 87654321 /out/x")
        self.assertEqual(substitute("-a <ADMIN_PIN>", public_tokens(tokens)), "-a <ADMIN_PIN>")


class ModeTests(unittest.TestCase):
    def test_select_targets_newest_unrevoked(self):
        self.assertEqual(modes.select_targets(BUNDLE), {"s": "S2" * 20, "e": "E1" * 20, "a": "A1" * 20})

    def test_missing_usage(self):
        bundle = KeyBundle(FP, "A" * 16, (), (sub("S1" * 20, "s", 1),), True)
        with self.assertRaises(RecoveryError):
            modes.select_targets(bundle)

    def test_expired(self):
        bundle = KeyBundle(FP, "A" * 16, (), (sub("S1" * 20, "s", 1, expires=50),
                                              sub("E1" * 20, "e", 1), sub("A1" * 20, "a", 1, expires=500)), True)
        targets = modes.select_targets(bundle)
        self.assertEqual(modes.expired(bundle, targets, now=100), ["S1" * 20])

    def test_default_algo(self):
        targets = modes.select_targets(BUNDLE)
        self.assertEqual(modes.default_algo(BUNDLE, targets), "ed25519")

    def test_clone_has_no_staging_steps(self):
        targets = modes.select_targets(BUNDLE)
        self.assertEqual(modes.mode_steps("clone", BUNDLE, targets, "2y", "ed25519", False), [])
        self.assertEqual(modes.backup_steps("clone", BUNDLE), [])

    def test_renew(self):
        targets = modes.select_targets(BUNDLE)
        (step,) = modes.mode_steps("renew", BUNDLE, targets, "3y", "ed25519", False)
        self.assertEqual(step.argv[step.argv.index("--quick-set-expire") + 1:],
                         (FP, "3y", "S2" * 20, "E1" * 20, "A1" * 20))
        self.assertEqual(step.stdin, ("<CERTIFY_PASS>",))

    def test_rotate_with_revoke(self):
        targets = modes.select_targets(BUNDLE)
        steps = modes.mode_steps("rotate", BUNDLE, targets, "2y", "ed25519", True)
        adds = [s.argv[-3:] for s in steps if "--quick-add-key" in s.argv]
        self.assertEqual(adds, [("ed25519", "sign", "2y"), ("cv25519", "encrypt", "2y"), ("ed25519", "auth", "2y")])
        revokes = [s for s in steps if "--edit-key" in s.argv]
        self.assertEqual([s.stdin[0] for s in revokes], [f"key {targets[u]}" for u in "sea"])
        self.assertEqual(revokes[0].stdin, (f"key {'S2' * 20}", "revkey", "y", "0", "", "y", "<CERTIFY_PASS>", "save"))
        for i, step in enumerate(steps):
            if "--edit-key" in step.argv:
                self.assertEqual(steps[i - 1].argv, ("gpgconf", "--kill", "all"))

    def test_backup_steps(self):
        steps = modes.backup_steps("renew", BUNDLE)
        outputs = [s.argv[s.argv.index("--output") + 1] for s in steps]
        kid = "A" * 16
        self.assertEqual(outputs, [f"<OUT>/secret-{kid}-Certify.key", f"<OUT>/secret-{kid}-Subkeys.key",
                                   f"<OUT>/public-{kid}-<DATE>.asc"])


class CardTests(unittest.TestCase):
    TARGETS = {"s": "S" * 40, "e": "E" * 40, "a": "T" * 40}

    def test_every_card_session_starts_after_kill(self):
        steps = card.card_steps(1, FP, self.TARGETS, card.CardSettings()) + card.verify_steps(1, FP, self.TARGETS)
        for i, step in enumerate(steps):
            scripted = step.argv[0] == "ykman" or any(
                flag in step.argv for flag in ("--change-pin", "--edit-card", "--edit-key"))
            if scripted:
                with self.subTest(step=" ".join(step.argv)):
                    self.assertEqual(steps[i - 1].argv, ("gpgconf", "--kill", "all"))
                    self.assertEqual(steps[i - 1].home, step.home)
            if step.argv[0] == "gpg":
                self.assertIn("--batch", step.argv)

    def test_keytocard_scripts(self):
        steps = [s for s in card.card_steps(2, FP, self.TARGETS, card.CardSettings()) if "--edit-key" in s.argv]
        self.assertEqual([s.stdin for s in steps], [
            (f"key {self.TARGETS[u]}", "keytocard", slot, "<CERTIFY_PASS>", "<ADMIN_PIN>", "save")
            for u, slot in (("s", "1"), ("e", "2"), ("a", "3"))])

    def test_serial_token_and_settings(self):
        settings = card.CardSettings(retries=("3", "3", "3"), touch={"sig": "cached", "dec": "on", "aut": "off"},
                                     name="Jane Q Doe", url="https://example.org/k.asc")
        steps = card.card_steps(2, FP, self.TARGETS, settings)
        text = format_plan(steps)
        self.assertIn("ykman --device <SERIAL2> openpgp reset -f", text)
        self.assertIn("set-retries 3 3 3 -f -a <DEFAULT_ADMIN_PIN>", text)
        argvs = [" ".join(s.argv) for s in steps]
        first_pin = next(i for i, a in enumerate(argvs) if "--change-pin" in a)
        self.assertLess(next(i for i, a in enumerate(argvs) if "set-retries" in a), first_pin)
        self.assertIn("set-touch sig cached -f -a <ADMIN_PIN>", text)
        self.assertIn("set-touch aut off -f -a <ADMIN_PIN>", text)
        holder = next(s for s in steps if "--edit-card" in s.argv)
        self.assertEqual(holder.stdin, ("admin", "login", "<LOGIN>", "<ADMIN_PIN>", "name", "<SURNAME>",
                                        "<GIVEN>", "url", "<URL>", "quit"))

    def test_minimal_cardholder(self):
        holder = next(s for s in card.card_steps(1, FP, self.TARGETS, card.CardSettings()) if "--edit-card" in s.argv)
        self.assertEqual(holder.stdin, ("admin", "login", "<LOGIN>", "<ADMIN_PIN>", "quit"))

    def test_verify_checks(self):
        steps = card.verify_steps(1, FP, self.TARGETS)
        self.assertEqual([s.check for s in steps if s.check], ["cardstatus", "roundtrip"])
        self.assertIn(f"{'S' * 40}!", steps[3].argv)

    def test_pins_and_login(self):
        admin, user = card.generate_pins()
        self.assertRegex(admin, r"^\d{8}$")
        self.assertRegex(user, r"^\d{6}$")
        self.assertRegex(card.random_login(), r"^yk\.[a-z0-9]{16}$")

    def test_split_name(self):
        self.assertEqual(card.split_name("Jane Q Doe"), ("Doe", "Jane Q"))
        self.assertEqual(card.split_name("Doe"), ("Doe", ""))
        self.assertEqual(card.split_name(""), ("", ""))

    def test_parsers(self):
        self.assertEqual(card.parse_serials("12345678\n\nnoise\n"), ["12345678"])
        self.assertEqual(card.parse_card_fprs("serial:1:\nfpr:aa:bb:cc:\n"), ["AA", "BB", "CC"])
        self.assertEqual(card.parse_card_fprs("serial:1:\n"), ["", "", ""])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd recovery && python3 -m unittest tests.test_steps -v`
Expected: ERROR, `ModuleNotFoundError: No module named 'ykrecover.plan'`.

- [ ] **Step 3: Implement `plan.py`**

`recovery/ykrecover/plan.py`:

```python
"""The canonical step list shared by execution and --dry-run.

recoverCard.sh must print byte-identical plans; see tests/test_plan_parity.py.
"""

from dataclasses import dataclass

STAGING = "<STAGING>"
CARDHOME = "<CARDHOME>"
VERIFYHOME = "<VERIFYHOME>"
SECRET_TOKENS = ("<CERTIFY_PASS>", "<ADMIN_PIN>", "<USER_PIN>")


@dataclass(frozen=True)
class Step:
    phase: str
    home: str
    argv: tuple
    stdin: tuple = ()
    check: str = ""


def format_plan(steps) -> str:
    lines = []
    for number, step in enumerate(steps, start=1):
        lines.append(f"STEP {number} {step.phase} {step.home} {' '.join(step.argv)}")
        lines.extend(f"STDIN {line}" for line in step.stdin)
        if step.check:
            lines.append(f"CHECK {step.check}")
    return "\n".join(lines) + "\n"


def substitute(text: str, tokens: dict) -> str:
    for token, value in tokens.items():
        text = text.replace(token, value)
    return text


def public_tokens(tokens: dict) -> dict:
    """Tokens safe to show: secrets stay as placeholders."""
    return {k: v for k, v in tokens.items() if k not in SECRET_TOKENS}
```

- [ ] **Step 4: Implement `modes.py`**

`recovery/ykrecover/modes.py`:

```python
"""clone / renew / rotate: which subkeys go to cards, and the staging steps that produce them."""

from . import EXIT_BACKUP, RecoveryError
from .plan import STAGING, Step

USAGES = ("s", "e", "a")
USAGE_WORD = {"s": "sign", "e": "encrypt", "a": "auth"}
ALGOS = {
    "rsa4096": {"s": "rsa4096", "e": "rsa4096", "a": "rsa4096"},
    "ed25519": {"s": "ed25519", "e": "cv25519", "a": "ed25519"},
}
ROTATED = {"s": "<NEW_FPR_S>", "e": "<NEW_FPR_E>", "a": "<NEW_FPR_A>"}
PASS = "<CERTIFY_PASS>"
LOOPBACK = ("gpg", "--batch", "--pinentry-mode", "loopback")


def select_targets(bundle) -> dict:
    """The newest non-revoked subkey for each usage."""
    targets = {}
    for usage in USAGES:
        usable = [k for k in bundle.subkeys if usage in k.usage and k.validity != "r"]
        if not usable:
            raise RecoveryError(f"key {bundle.key_fp} has no {USAGE_WORD[usage]} subkey", EXIT_BACKUP)
        targets[usage] = max(usable, key=lambda k: k.created).fpr
    return targets


def unique(values) -> list:
    return list(dict.fromkeys(values))


def expired(bundle, targets, now: int) -> list:
    by_fpr = {k.fpr: k for k in bundle.subkeys}
    return [f for f in unique(targets[u] for u in USAGES) if 0 < by_fpr[f].expires <= now]


def default_algo(bundle, targets) -> str:
    algo = next(k.algo for k in bundle.subkeys if k.fpr == targets["s"])
    return "rsa4096" if algo.startswith("rsa") else "ed25519"


def mode_steps(mode, bundle, targets, expire, algo, revoke_old) -> list:
    fp = bundle.key_fp
    if mode == "renew":
        fprs = unique(targets[u] for u in USAGES)
        return [Step("mode", STAGING, (*LOOPBACK, "--passphrase-fd", "0", "--quick-set-expire", fp, expire, *fprs),
                     (PASS,))]
    if mode != "rotate":
        return []
    steps = [Step("mode", STAGING, (*LOOPBACK, "--passphrase-fd", "0", "--quick-add-key", fp, ALGOS[algo][u],
                                    USAGE_WORD[u], expire), (PASS,)) for u in USAGES]
    if revoke_old:
        for old in unique(targets[u] for u in USAGES):
            steps.append(Step("mode", STAGING, ("gpgconf", "--kill", "all")))
            steps.append(Step("mode", STAGING, (*LOOPBACK, "--command-fd", "0", "--edit-key", fp),
                              (f"key {old}", "revkey", "y", "0", "", "y", PASS, "save")))
    return steps


def backup_steps(mode, bundle) -> list:
    """renew/rotate change key material, so a new backup is written before any card."""
    if mode == "clone":
        return []
    fp, kid = bundle.key_fp, bundle.key_id
    return [
        Step("backup", STAGING, (*LOOPBACK, "--passphrase-fd", "0", "--yes", "--armor", "--output",
                                 f"<OUT>/secret-{kid}-Certify.key", "--export-secret-keys", fp), (PASS,)),
        Step("backup", STAGING, (*LOOPBACK, "--passphrase-fd", "0", "--yes", "--armor", "--output",
                                 f"<OUT>/secret-{kid}-Subkeys.key", "--export-secret-subkeys", fp), (PASS,)),
        Step("backup", STAGING, ("gpg", "--batch", "--yes", "--armor", "--output",
                                 f"<OUT>/public-{kid}-<DATE>.asc", "--export", fp)),
    ]


def finish_steps(key_fp, key_id, targets) -> list:
    return [
        Step("finish", STAGING, ("gpg", "--batch", "--yes", "--armor", "--output",
                                 f"<OUT>/public-{key_id}-<DATE>.asc", "--export", key_fp)),
        Step("finish", STAGING, ("gpg", "--batch", "--yes", "--output", f"<OUT>/ssh-{key_id}.pub",
                                 "--export-ssh-key", f"{targets['a']}!")),
    ]
```

- [ ] **Step 5: Implement `card.py`**

`recovery/ykrecover/card.py`:

```python
"""Per-card write and verify steps, card discovery and card-status parsing."""

import secrets
import string
from dataclasses import dataclass, field

from .plan import CARDHOME, STAGING, VERIFYHOME, Step

DEFAULT_ADMIN_PIN = "12345678"
DEFAULT_USER_PIN = "123456"
SLOTS = (("s", "1", "sig"), ("e", "2", "dec"), ("a", "3", "aut"))
TOUCH_POLICIES = ("off", "on", "fixed", "cached", "cached-fixed")
CARD_GPG = ("gpg", "--batch", "--command-fd", "0", "--pinentry-mode", "loopback")


@dataclass(frozen=True)
class CardSettings:
    retries: tuple = ("5", "5", "5")
    touch: dict = field(default_factory=lambda: {"sig": "on", "dec": "on", "aut": "on"})
    name: str = ""
    url: str = ""


def generate_pins() -> tuple:
    """The guide's method: 8-digit Admin PIN, 6-digit User PIN."""
    digits = "".join(secrets.choice(string.digits) for _ in range(14))
    return digits[:8], digits[8:]


def random_login() -> str:
    """Non-identifying login, as the guide recommends."""
    return "yk." + "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(16))


def split_name(name: str) -> tuple:
    parts = name.split()
    return (parts[-1], " ".join(parts[:-1])) if parts else ("", "")


def card_steps(index: int, key_fp: str, targets: dict, settings: CardSettings) -> list:
    phase, serial = f"card{index}", f"<SERIAL{index}>"
    kill = Step(phase, CARDHOME, ("gpgconf", "--kill", "all"))
    holder = ["admin", "login", "<LOGIN>", "<ADMIN_PIN>"]
    if settings.name:
        holder += ["name", "<SURNAME>", "<GIVEN>"]
    if settings.url:
        holder += ["url", "<URL>"]
    steps = [
        kill, Step(phase, CARDHOME, ("ykman", "--device", serial, "openpgp", "reset", "-f")),
        # Retries first: changing the retry counters may reset the PINs to defaults.
        kill, Step(phase, CARDHOME, ("ykman", "--device", serial, "openpgp", "access", "set-retries",
                                     *settings.retries, "-f", "-a", "<DEFAULT_ADMIN_PIN>")),
        kill, Step(phase, CARDHOME, (*CARD_GPG, "--change-pin"),
                   ("3", "<DEFAULT_ADMIN_PIN>", "<ADMIN_PIN>", "<ADMIN_PIN>", "q")),
        kill, Step(phase, CARDHOME, (*CARD_GPG, "--change-pin"),
                   ("1", "<DEFAULT_USER_PIN>", "<USER_PIN>", "<USER_PIN>", "q")),
        kill, Step(phase, CARDHOME, (*CARD_GPG, "--edit-card"), (*holder, "quit")),
    ]
    for usage, slot, _ in SLOTS:
        steps += [kill, Step(phase, CARDHOME, (*CARD_GPG, "--edit-key", key_fp),
                             (f"key {targets[usage]}", "keytocard", slot, "<CERTIFY_PASS>", "<ADMIN_PIN>", "save"))]
    for _, _, ref in SLOTS:
        steps += [kill, Step(phase, CARDHOME, ("ykman", "--device", serial, "openpgp", "keys", "set-touch", ref,
                                               settings.touch[ref], "-f", "-a", "<ADMIN_PIN>"))]
    return steps


def verify_steps(index: int, key_fp: str, targets: dict) -> list:
    phase, v = f"verify{index}", VERIFYHOME
    pin = ("gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase-fd", "0", "--yes")
    return [
        Step(phase, STAGING, ("gpg", "--batch", "--yes", "--armor", "--output", f"{v}/pub.asc", "--export", key_fp)),
        Step(phase, v, ("gpg", "--batch", "--import", f"{v}/pub.asc")),
        Step(phase, v, ("gpg", "--batch", "--with-colons", "--card-status"), check="cardstatus"),
        Step(phase, v, (*pin, "--local-user", f"{targets['s']}!", "--output", f"{v}/test.sig", "--detach-sign",
                        f"{v}/test.txt"), ("<USER_PIN>",)),
        Step(phase, v, ("gpg", "--batch", "--verify", f"{v}/test.sig", f"{v}/test.txt")),
        Step(phase, v, ("gpg", "--batch", "--yes", "--trust-model", "always", "--recipient", f"{targets['e']}!",
                        "--output", f"{v}/test.gpg", "--encrypt", f"{v}/test.txt")),
        Step(phase, v, ("gpgconf", "--kill", "all")),
        Step(phase, v, (*pin, "--output", f"{v}/test.out", "--decrypt", f"{v}/test.gpg"), ("<USER_PIN>",),
             check="roundtrip"),
    ]


def parse_serials(text: str) -> list:
    return [line.strip() for line in text.splitlines() if line.strip().isdigit()]


def parse_card_fprs(colons: str) -> list:
    for line in colons.splitlines():
        if line.startswith("fpr:"):
            return [f.upper() for f in line.split(":")[1:4]]
    return ["", "", ""]
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd recovery && python3 -m unittest tests.test_steps -v`
Expected: `OK` (18 tests).

- [ ] **Step 7: Commit**

```bash
git add recovery/ykrecover/plan.py recovery/ykrecover/modes.py recovery/ykrecover/card.py recovery/tests/test_steps.py
git commit -m "recovery: canonical step builders for modes, cards and verification"
```

---

### Task 4: Session setup, dry-run, CLI and golden plans (Python)

**Files:**

- Create: `recovery/ykrecover/migrate.py`
- Create: `recovery/ykrecover/cli.py`
- Create: `recovery/ykrecover/__main__.py`
- Create (generated): `recovery/tests/golden/*.plan`
- Test: `recovery/tests/test_cli_contract.py`, `recovery/tests/test_plan_parity.py`

**Interfaces:**

- Consumes: everything from Tasks 1–3.
- Produces (`migrate.py`): `Options` dataclass (fields below), `CardResult(index, serial, status, detail, admin_pin, user_pin)`, `StepError(shown, stderr)` with `.shown`, `network_interfaces_up() -> list[str]`, `class Session(opts, ui)` with `validate_options()`, `preflight()`, `load() -> KeyBundle`, `validate_bundle()`, `describe() -> str`, `unlock(passphrase) -> bool`, `staging_steps() -> list[Step]`, `card_plan(index, targets) -> list[Step]`, `dry_run_text() -> str`, `cleanup()`. Also `run(opts, ui) -> int`. `Session.execute()` comes in Task 6, and `run()` calls it only for non-dry runs, which no Task 4 test performs.
- UI protocol (implemented by `cli.CliUI` here and `gui.TkUI` in Task 8): `output(text)`, `info(msg)`, `warn(msg)`, `error(msg)`, `ask_secret(prompt) -> str`, `confirm(prompt) -> bool`, `confirm_reset(serial) -> bool`, `wait_for_card(index, total)`, `card_failed(index, serial, step, error) -> "retry"|"skip"|"abort"`, `progress(index, status)`, `show_pins(list[(serial, admin, user)])`. `ask_secret`/`confirm*`/`wait_for_card`/`card_failed` raise `Aborted` on EOF or cancel.
- CLI prompt texts (contract with bash, used by tests): `Certify key passphrase:`, `Write subkeys of <FP> to <N> card(s)? [y/N]`, `New backup written to <OUT>. Saved it to offline storage? [y/N]`, `Admin PIN (<label>):`, `Repeat Admin PIN (<label>):`, `User PIN (<label>):`, `Repeat User PIN (<label>):` (label `card <i>` or `all cards`), `Insert card <i> of <N> and press Enter`, `Type RESET <serial> to erase card <serial>:`, `[r]etry, [s]kip or [a]bort?`. Answers are compared exactly; `y`/`yes` (any case) for yes.

- [ ] **Step 1: Write the failing contract and parity tests**

`recovery/tests/test_cli_contract.py`:

```python
import shutil
import unittest

from tests.helpers import FIXTURES, run_impl, short_tempdir

ED_EXPORT = str(FIXTURES / "ed25519" / "export")


class ContractTests:
    """Option validation and dry-run behaviour shared by both implementations."""

    IMPL = ""

    def cli(self, *args, stdin=""):
        return run_impl(self.IMPL, list(args), stdin)

    def test_missing_backup_is_usage_error(self):
        self.assertEqual(self.cli("--mode", "clone").returncode, 1)

    def test_bad_choice_is_usage_error(self):
        self.assertEqual(self.cli("--backup", ED_EXPORT, "--mode", "bogus", "--dry-run").returncode, 1)
        self.assertEqual(self.cli("--backup", ED_EXPORT, "--retries", "0,5,5", "--dry-run").returncode, 1)
        self.assertEqual(self.cli("--backup", ED_EXPORT, "--cards", "0", "--dry-run").returncode, 1)

    def test_algo_requires_rotate(self):
        self.assertEqual(self.cli("--backup", ED_EXPORT, "--algo", "ed25519", "--dry-run").returncode, 1)

    def test_out_required_for_live_run(self):
        self.assertEqual(self.cli("--backup", ED_EXPORT).returncode, 1)

    def test_out_inside_backup_rejected(self):
        self.assertEqual(self.cli("--backup", ED_EXPORT, "--out", f"{ED_EXPORT}/new").returncode, 1)

    def test_out_with_existing_keys_rejected(self):
        out = short_tempdir(self, "out")
        (out / "secret-0000-Certify.key").write_text("old")
        self.assertEqual(self.cli("--backup", ED_EXPORT, "--out", str(out)).returncode, 1)

    def test_backup_not_found(self):
        self.assertEqual(self.cli("--backup", "/nonexistent/backup", "--dry-run").returncode, 2)

    def test_ambiguous_backup_needs_source(self):
        root = FIXTURES / "ed25519"
        self.assertEqual(self.cli("--backup", str(root), "--dry-run").returncode, 2)
        result = self.cli("--backup", str(root), "--source", "gnupghome", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_subkeys_only_cannot_renew(self):
        result = self.cli("--backup", str(FIXTURES / "ed25519" / "subkeys-only"), "--mode", "renew", "--dry-run")
        self.assertEqual(result.returncode, 2)

    def test_expired_needs_renew(self):
        expired = str(FIXTURES / "expired" / "export")
        self.assertEqual(self.cli("--backup", expired, "--dry-run").returncode, 2)
        self.assertEqual(self.cli("--backup", expired, "--mode", "renew", "--dry-run").returncode, 0)

    def test_read_only_backup(self):
        copy = short_tempdir(self, "ro") / "backup"
        shutil.copytree(FIXTURES / "ed25519" / "gnupghome", copy)
        for p in [copy, *copy.rglob("*")]:
            p.chmod(0o500 if p.is_dir() else 0o400)
        result = self.cli("--backup", str(copy), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dry_run_prints_only_the_plan(self):
        result = self.cli("--backup", ED_EXPORT, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("STEP 1 card1 <CARDHOME> gpgconf --kill all\n"))


class PythonContractTests(ContractTests, unittest.TestCase):
    IMPL = "python"


if __name__ == "__main__":
    unittest.main()
```

`recovery/tests/test_plan_parity.py`:

```python
import os
import unittest

from tests.helpers import GOLDEN, run_impl

CASES = {
    "clone-ed25519-export": ["--backup", "tests/fixtures/ed25519/export"],
    "clone-ed25519-gnupghome": ["--backup", "tests/fixtures/ed25519/gnupghome"],
    "clone-ed25519-subkeys-only": ["--backup", "tests/fixtures/ed25519/subkeys-only"],
    "clone-rsa-two-cards-settings": [
        "--backup", "tests/fixtures/rsa4096/export", "--cards", "2", "--retries", "3,3,3", "--touch", "cached",
        "--touch-aut", "off", "--name", "Jane Q Doe", "--url", "https://example.org/key.asc"],
    "renew-ed25519": ["--backup", "tests/fixtures/ed25519/export", "--mode", "renew", "--expire", "3y"],
    "renew-expired": ["--backup", "tests/fixtures/expired/export", "--mode", "renew"],
    "rotate-rsa-to-ed25519-revoke": [
        "--backup", "tests/fixtures/rsa4096/export", "--mode", "rotate", "--algo", "ed25519", "--revoke-old"],
}


class PlanParityTests:
    """Dry-run plans must equal the checked-in golden files, for every implementation."""

    IMPL = ""

    def test_plans_match_golden(self):
        for name, args in CASES.items():
            with self.subTest(name):
                result = run_impl(self.IMPL, [*args, "--dry-run"])
                self.assertEqual(result.returncode, 0, result.stderr)
                golden = GOLDEN / f"{name}.plan"
                if os.environ.get("UPDATE_GOLDEN") and self.IMPL == "python":
                    GOLDEN.mkdir(exist_ok=True)
                    golden.write_text(result.stdout)
                self.assertEqual(result.stdout, golden.read_text())


class PythonPlanParityTests(PlanParityTests, unittest.TestCase):
    IMPL = "python"


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd recovery && python3 -m unittest tests.test_cli_contract tests.test_plan_parity -v`
Expected: FAIL, with every return code 1 and stderr `No module named ykrecover.__main__`.

- [ ] **Step 3: Implement `migrate.py` (setup and dry-run parts)**

`recovery/ykrecover/migrate.py`:

```python
"""One recovery run: preflight, backup, mode, cards, finish.

The CLI (run) and the GUI drive the same Session methods.
"""

import datetime
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import EXIT_BACKUP, EXIT_INTERRUPT, EXIT_OK, EXIT_USAGE, Aborted, RecoveryError
from . import backup, card, modes
from .gpg import Home, base_is_tmpfs, gpg_version, runtime_base
from .plan import format_plan


@dataclass
class Options:
    backup: Path
    mode: str = "clone"
    cards: int = 1
    out: Path = None
    expire: str = "2y"
    algo: str = ""
    revoke_old: bool = False
    pins: str = "generate"
    same_pins: bool = False
    retries: tuple = ("5", "5", "5")
    touch: dict = field(default_factory=lambda: {"sig": "on", "dec": "on", "aut": "on"})
    name: str = ""
    login: str = ""
    url: str = ""
    source: str = ""
    key: str = ""
    dry_run: bool = False
    yes: bool = False


@dataclass
class CardResult:
    index: int
    serial: str = ""
    status: str = "pending"  # OK, FAILED or SKIPPED once decided
    detail: str = ""
    admin_pin: str = ""
    user_pin: str = ""


class StepError(Exception):
    def __init__(self, shown: str, stderr: str):
        super().__init__(f"{shown}\n{stderr.strip()[-2000:]}")
        self.shown = shown


def network_interfaces_up() -> list:
    up = []
    for iface in sorted(Path("/sys/class/net").glob("*")):
        try:
            state = (iface / "operstate").read_text().strip()
        except OSError:
            continue
        if iface.name != "lo" and state == "up":
            up.append(iface.name)
    return up


class Session:
    def __init__(self, opts: Options, ui):
        self.opts, self.ui = opts, ui
        self.staging = None
        self.bundle = None
        self.targets = {}
        self.passphrase = ""
        self.results = []
        self.logins = {}

    def validate_options(self) -> None:
        o = self.opts
        if o.mode not in ("clone", "renew", "rotate"):
            raise RecoveryError(f"unknown mode {o.mode}", EXIT_USAGE)
        if o.cards < 1:
            raise RecoveryError("--cards must be 1 or more", EXIT_USAGE)
        if o.algo and o.algo not in modes.ALGOS:
            raise RecoveryError(f"unknown algorithm {o.algo}", EXIT_USAGE)
        if (o.algo or o.revoke_old) and o.mode != "rotate":
            raise RecoveryError("--algo and --revoke-old need --mode rotate", EXIT_USAGE)
        if o.source and o.source not in backup.KINDS:
            raise RecoveryError(f"unknown source {o.source}", EXIT_USAGE)
        if len(o.retries) != 3 or not all(r.isdigit() and 1 <= int(r) <= 99 for r in o.retries):
            raise RecoveryError("retries must be three numbers from 1 to 99", EXIT_USAGE)
        if any(p not in card.TOUCH_POLICIES for p in o.touch.values()):
            raise RecoveryError("unknown touch policy", EXIT_USAGE)
        if o.dry_run:
            return
        if o.out is None:
            raise RecoveryError("--out is required", EXIT_USAGE)
        out, src = o.out.resolve(), o.backup.resolve()
        if out == src or src in out.parents:
            raise RecoveryError("--out must be outside the backup folder", EXIT_USAGE)
        if out.is_dir() and any(out.glob("secret-*.key")):
            raise RecoveryError(f"{out} already holds key files; choose an empty folder", EXIT_USAGE)

    def preflight(self) -> None:
        tools = ["gpg", "gpgconf"] if self.opts.dry_run else ["gpg", "gpgconf", "gpg-connect-agent", "ykman"]
        missing = [t for t in tools if shutil.which(t) is None]
        if missing:
            raise RecoveryError(f"missing tools: {' '.join(missing)}", EXIT_USAGE)
        if gpg_version() < (2, 2):
            raise RecoveryError("gpg 2.2 or newer is required", EXIT_USAGE)
        if self.opts.dry_run:
            return
        for iface in network_interfaces_up():
            self.ui.warn(f"network interface {iface} is up; an air-gapped system is recommended")
        if not base_is_tmpfs(runtime_base()):
            self.ui.warn(f"{runtime_base()} is not tmpfs; key material may touch disk")

    def load(self) -> backup.KeyBundle:
        candidate = backup.choose(backup.detect(self.opts.backup), self.opts.source or None)
        if self.staging:
            self.staging.destroy()
        self.staging = Home.create("staging")
        self.bundle = backup.choose_key(backup.load(candidate, self.staging), self.opts.key or None)
        self.targets = modes.select_targets(self.bundle)
        return self.bundle

    def validate_bundle(self) -> None:
        mode = self.opts.mode
        if mode != "clone" and not self.bundle.has_certify_secret:
            raise RecoveryError(f"--mode {mode} needs the Certify key, but this backup only has subkeys",
                                EXIT_BACKUP)
        stale = modes.expired(self.bundle, self.targets, int(time.time()))
        if stale and mode == "clone":
            raise RecoveryError(f"subkeys have expired: {' '.join(stale)}; use --mode renew or --mode rotate",
                                EXIT_BACKUP)

    def describe(self) -> str:
        b = self.bundle
        lines = [f"Key {b.key_fp}", *(f"  uid {u}" for u in b.uids)]
        for k in b.subkeys:
            mark = " <- card" if k.fpr in self.targets.values() else ""
            expires = datetime.date.fromtimestamp(k.expires).isoformat() if k.expires else "never"
            lines.append(f"  [{k.usage.upper()}] {k.algo} {k.fpr} expires {expires}{mark}")
        return "\n".join(lines)

    def unlock(self, passphrase: str) -> bool:
        if backup.check_passphrase(self.staging, self.bundle, passphrase):
            self.passphrase = passphrase
            return True
        return False

    def _settings(self) -> card.CardSettings:
        o = self.opts
        return card.CardSettings(retries=tuple(o.retries), touch=dict(o.touch), name=o.name, url=o.url)

    def staging_steps(self) -> list:
        o, b = self.opts, self.bundle
        algo = o.algo or modes.default_algo(b, self.targets)
        return modes.mode_steps(o.mode, b, self.targets, o.expire, algo, o.revoke_old) + modes.backup_steps(o.mode, b)

    def card_plan(self, index: int, targets: dict) -> list:
        fp = self.bundle.key_fp
        return card.card_steps(index, fp, targets, self._settings()) + card.verify_steps(index, fp, targets)

    def dry_run_text(self) -> str:
        targets = modes.ROTATED if self.opts.mode == "rotate" else self.targets
        steps = self.staging_steps()
        for index in range(1, self.opts.cards + 1):
            steps += self.card_plan(index, targets)
        steps += modes.finish_steps(self.bundle.key_fp, self.bundle.key_id, targets)
        return format_plan(steps)

    def cleanup(self) -> None:
        if self.staging:
            self.staging.destroy()
            self.staging = None
        self.passphrase = ""


def run(opts: Options, ui) -> int:
    session = Session(opts, ui)
    try:
        session.validate_options()
        session.preflight()
        session.load()
        session.validate_bundle()
        if opts.dry_run:
            ui.output(session.dry_run_text())
            return EXIT_OK
        ui.info(session.describe())
        for _ in range(3):
            if session.unlock(ui.ask_secret("Certify key passphrase:")):
                break
            ui.warn("wrong passphrase")
        else:
            raise RecoveryError("wrong passphrase three times", EXIT_BACKUP)
        if not opts.yes and not ui.confirm(f"Write subkeys of {session.bundle.key_fp} to {opts.cards} card(s)?"):
            return EXIT_INTERRUPT
        return session.execute()
    except RecoveryError as err:
        ui.error(str(err))
        return err.code
    except (Aborted, KeyboardInterrupt):
        ui.error("aborted")
        return EXIT_INTERRUPT
    finally:
        session.cleanup()
```

- [ ] **Step 4: Implement `cli.py` and `__main__.py`**

`recovery/ykrecover/cli.py`:

```python
"""Terminal front end: python3 -m ykrecover --cli [options] | --cli update-stubs [options]."""

import argparse
import getpass
import sys
from pathlib import Path

from . import EXIT_USAGE, Aborted
from .card import TOUCH_POLICIES
from .migrate import Options, run


class CliUI:
    """Prompts on stderr, answers from stdin. Secrets use getpass only on a TTY."""

    def _line(self, prompt: str) -> str:
        sys.stderr.write(prompt + " ")
        sys.stderr.flush()
        line = sys.stdin.readline()
        if not line:
            sys.stderr.write("\n")
            raise Aborted()
        return line.rstrip("\n")

    def output(self, text):
        sys.stdout.write(text)

    def info(self, msg):
        print(msg, file=sys.stderr)

    def warn(self, msg):
        print(f"warning: {msg}", file=sys.stderr)

    def error(self, msg):
        print(f"error: {msg}", file=sys.stderr)

    def ask_secret(self, prompt):
        if sys.stdin.isatty():
            try:
                return getpass.getpass(prompt + " ")
            except EOFError as err:
                raise Aborted() from err
        return self._line(prompt)

    def confirm(self, prompt):
        return self._line(f"{prompt} [y/N]").lower() in ("y", "yes")

    def confirm_reset(self, serial):
        return self._line(f"Type RESET {serial} to erase card {serial}:") == f"RESET {serial}"

    def wait_for_card(self, index, total):
        self._line(f"Insert card {index} of {total} and press Enter")

    def card_failed(self, index, serial, step, error):
        self.warn(f"card {serial} failed at: {step}\n{error}")
        while True:
            answer = self._line("[r]etry, [s]kip or [a]bort?").lower()[:1]
            if answer in ("r", "s", "a"):
                return {"r": "retry", "s": "skip", "a": "abort"}[answer]

    def progress(self, index, status):
        print(f"[card {index}] {status}", file=sys.stderr)

    def show_pins(self, pins):
        for serial, admin, user in pins:
            print(f"Card {serial}: Admin PIN {admin}  User PIN {user}", file=sys.stderr)


def _retries(text: str) -> tuple:
    parts = tuple(text.split(","))
    if len(parts) != 3 or not all(p.isdigit() and 1 <= int(p) <= 99 for p in parts):
        raise argparse.ArgumentTypeError("expected U,R,A such as 5,5,5")
    return parts


def _positive(text: str) -> int:
    if not text.isdigit():
        raise argparse.ArgumentTypeError("expected a number")
    return int(text)


def migrate_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ykrecover --cli", description="Migrate GnuPG key backups to new YubiKeys.")
    p.add_argument("--backup", type=Path, required=True)
    p.add_argument("--mode", choices=("clone", "renew", "rotate"), default="clone")
    p.add_argument("--cards", type=_positive, default=1)
    p.add_argument("--out", type=Path)
    p.add_argument("--expire", default="2y")
    p.add_argument("--algo", choices=("rsa4096", "ed25519"), default="")
    p.add_argument("--revoke-old", action="store_true")
    p.add_argument("--pins", choices=("generate", "enter"), default="generate")
    p.add_argument("--same-pins", action="store_true")
    p.add_argument("--retries", type=_retries, default=("5", "5", "5"))
    p.add_argument("--touch", choices=TOUCH_POLICIES, default="on")
    p.add_argument("--touch-sig", choices=TOUCH_POLICIES)
    p.add_argument("--touch-enc", choices=TOUCH_POLICIES)
    p.add_argument("--touch-aut", choices=TOUCH_POLICIES)
    p.add_argument("--name", default="")
    p.add_argument("--login", default="")
    p.add_argument("--url", default="")
    p.add_argument("--source", choices=("export", "subkeys-only", "gnupghome"), default="")
    p.add_argument("--key", default="")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true")
    return p


def stubs_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ykrecover --cli update-stubs",
                                description="Point the daily keyring at the new cards.")
    p.add_argument("--import", dest="import_file", type=Path)
    p.add_argument("--remove-serial", action="append", default=[])
    p.add_argument("--cards", type=_positive, default=1)
    return p


def _parse(parser: argparse.ArgumentParser, argv: list):
    try:
        return parser.parse_args(argv), None
    except SystemExit as exit_:
        return None, (EXIT_USAGE if exit_.code else 0)


def main(argv: list) -> int:
    if argv[:1] == ["update-stubs"]:
        args, code = _parse(stubs_parser(), argv[1:])
        if args is None:
            return code
        from .stubs import update_stubs
        return update_stubs(CliUI(), args.import_file, args.remove_serial, args.cards)
    args, code = _parse(migrate_parser(), argv)
    if args is None:
        return code
    touch = {"sig": args.touch_sig or args.touch, "dec": args.touch_enc or args.touch,
             "aut": args.touch_aut or args.touch}
    opts = Options(backup=args.backup, mode=args.mode, cards=args.cards, out=args.out, expire=args.expire,
                   algo=args.algo, revoke_old=args.revoke_old, pins=args.pins, same_pins=args.same_pins,
                   retries=args.retries, touch=touch, name=args.name, login=args.login, url=args.url,
                   source=args.source, key=args.key, dry_run=args.dry_run, yes=args.yes)
    return run(opts, CliUI())
```

`recovery/ykrecover/__main__.py`:

```python
"""python3 -m ykrecover → GUI; python3 -m ykrecover --cli ... → terminal."""

import sys


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["--cli"]:
        from .cli import main as cli_main
        return cli_main(args[1:])
    from .gui import main as gui_main
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
```

(`ykrecover.stubs` arrives in Task 7 and is imported lazily, so nothing breaks before then.)

- [ ] **Step 5: Run the contract tests**

Run: `cd recovery && python3 -m unittest tests.test_cli_contract -v`
Expected: `OK` (12 tests). `test_out_with_existing_keys_rejected` and `test_out_inside_backup_rejected` return 1 from `validate_options`, before `run()` reaches `execute()`.

- [ ] **Step 6: Generate and inspect the golden plans**

Run: `cd recovery && UPDATE_GOLDEN=1 python3 -m unittest tests.test_plan_parity -v`
Expected: `OK`, and 7 files in `recovery/tests/golden/`.

Then inspect: `head -40 recovery/tests/golden/renew-ed25519.plan` and `grep -c '' recovery/tests/golden/*.plan`.
Check by eye:

- the plan opens with `STEP 1 mode <STAGING> gpg --batch --pinentry-mode loopback --passphrase-fd 0 --quick-set-expire <FP> 3y …`;
- no absolute paths appear (`grep -l "$PWD\|/tmp\|/run" recovery/tests/golden/*.plan` prints nothing);
- no PIN or passphrase values appear, only `<…>` tokens;
- the rotate plan's card steps use `key <NEW_FPR_S>` and its revoke steps use the real old fingerprints.

- [ ] **Step 7: Run the parity test without regenerating**

Run: `cd recovery && python3 -m unittest tests.test_plan_parity -v`
Expected: `OK`.

- [ ] **Step 8: Commit**

```bash
git add recovery/ykrecover/migrate.py recovery/ykrecover/cli.py recovery/ykrecover/__main__.py recovery/tests/test_cli_contract.py recovery/tests/test_plan_parity.py recovery/tests/golden
git commit -m "recovery: session setup, dry-run plans and CLI"
```

---

### Task 5: Apply clone/renew/rotate in staging (real gpg)

**Files:**

- Modify: `recovery/ykrecover/migrate.py` (add imports and `Session._tokens`, `_exec`, `apply_mode`)
- Test: `recovery/tests/test_modes_live.py`

**Interfaces:**

- Consumes: `Session` (Task 4), `backup.list_bundles`, `modes.*`.
- Produces: `Session._tokens() -> dict`, `Session._exec(step, homes: dict[str, Home], tokens) -> str` (stdout; raises `StepError`), `Session.apply_mode()` (runs mode and backup steps, then reloads `bundle`/`targets`; raises `RecoveryError(EXIT_BACKUP)` on failure).

- [ ] **Step 1: Write the failing tests**

`recovery/tests/test_modes_live.py`:

```python
import time
import unittest

from tests.helpers import FIXTURES, PASSPHRASE, fixture_info, short_tempdir
from ykrecover import backup
from ykrecover.gpg import Home
from ykrecover.migrate import Options, Session


class SilentUI:
    def warn(self, msg): pass
    def info(self, msg): pass


class ApplyModeTests(unittest.TestCase):
    def session(self, profile, **opts):
        out = short_tempdir(self, "out")
        s = Session(Options(backup=FIXTURES / profile / "export", out=out, **opts), SilentUI())
        self.addCleanup(s.cleanup)
        s.load()
        self.assertTrue(s.unlock(PASSPHRASE))
        return s, out

    def exported(self, path):
        home = Home.create("check")
        self.addCleanup(home.destroy)
        home.run(["gpg", "--batch", "--import", str(path)])
        return backup.choose_key(backup.list_bundles(home))

    def test_renew_extends_expiry_and_writes_backup(self):
        info = fixture_info("expired")
        s, out = self.session("expired", mode="renew", expire="3y")
        s.apply_mode()
        self.assertEqual(s.targets["s"], info["FPR_S"])
        kid = info["KEY_ID"]
        for name in (f"secret-{kid}-Certify.key", f"secret-{kid}-Subkeys.key"):
            self.assertTrue((out / name).is_file(), name)
        renewed = self.exported(out / f"secret-{kid}-Certify.key")
        self.assertTrue(all(k.expires > time.time() for k in renewed.subkeys))

    def test_rotate_with_revoke(self):
        info = fixture_info("rsa4096")
        s, out = self.session("rsa4096", mode="rotate", algo="ed25519", revoke_old=True)
        s.apply_mode()
        self.assertNotIn(s.targets["s"], {info["FPR_S"], info["FPR_E"], info["FPR_A"]})
        rotated = self.exported(out / f"secret-{info['KEY_ID']}-Certify.key")
        by_fpr = {k.fpr: k for k in rotated.subkeys}
        self.assertEqual({by_fpr[info[f]].validity for f in ("FPR_S", "FPR_E", "FPR_A")}, {"r"})
        self.assertEqual(by_fpr[s.targets["e"]].algo, "cv25519")

    def test_clone_changes_nothing(self):
        info = fixture_info("ed25519")
        s, out = self.session("ed25519")
        s.apply_mode()
        self.assertEqual(s.targets, {"s": info["FPR_S"], "e": info["FPR_E"], "a": info["FPR_A"]})
        self.assertEqual(list(out.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd recovery && python3 -m unittest tests.test_modes_live -v`
Expected: ERROR, `AttributeError: 'Session' object has no attribute 'apply_mode'`.

- [ ] **Step 3: Implement token substitution, step execution and `apply_mode`**

In `recovery/ykrecover/migrate.py`, change the plan import to:

```python
from .plan import STAGING, format_plan, public_tokens, substitute
```

Add these methods to `Session` (after `card_plan`):

```python
    def _tokens(self) -> dict:
        return {
            "<CERTIFY_PASS>": self.passphrase,
            "<OUT>": str(self.opts.out),
            "<DATE>": datetime.date.today().isoformat(),
            "<DEFAULT_ADMIN_PIN>": card.DEFAULT_ADMIN_PIN,
            "<DEFAULT_USER_PIN>": card.DEFAULT_USER_PIN,
        }

    def _exec(self, step, homes: dict, tokens: dict) -> str:
        shown = substitute(" ".join(step.argv), public_tokens(tokens))
        argv = [substitute(arg, tokens) for arg in step.argv]
        stdin = [substitute(line, tokens) for line in step.stdin]
        result = homes[step.home].run(argv, stdin)
        if result.returncode != 0:
            raise StepError(shown, result.stderr)
        return result.stdout

    def apply_mode(self) -> None:
        tokens, homes = self._tokens(), {STAGING: self.staging}
        for step in self.staging_steps():
            try:
                self._exec(step, homes, tokens)
            except StepError as err:
                raise RecoveryError(f"{self.opts.mode} failed: {err}", EXIT_BACKUP) from err
        if self.opts.mode == "clone":
            return
        self.bundle = backup.choose_key(backup.list_bundles(self.staging), self.bundle.key_fp)
        self.targets = modes.select_targets(self.bundle)
        self.staging.kill_agents()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd recovery && python3 -m unittest tests.test_modes_live -v`
Expected: `OK` (3 tests). If `test_rotate_with_revoke` fails, rerun the revoke script by hand with `--status-fd 1` and compare the `GET_*` prompt order against the spec's verified sequence before changing code.

- [ ] **Step 5: Commit**

```bash
git add recovery/ykrecover/migrate.py recovery/tests/test_modes_live.py
git commit -m "recovery: apply renew and rotate in staging and export the new backup"
```

---

### Task 6: Card writing with fake hardware (Python)

**Files:**

- Create: `recovery/tests/fakebin/gpg`, `recovery/tests/fakebin/ykman`, `recovery/tests/fakebin/gpg-connect-agent` (executable)
- Modify: `recovery/ykrecover/migrate.py` (add `execute`, `assign_pins`, `_ask_pin`, `write_cards`, `_wait_for_card`, `_write_card`, `_check`, `finish`)
- Test: `recovery/tests/test_fake_hardware.py`

**Interfaces:**

- Consumes: `Session._exec`, `apply_mode` (Task 5), `card.*` (Task 3).
- Produces: `Session.execute() -> int`, `Session.assign_pins()`, `Session.write_cards()`, `Session.finish() -> int`, `Session.results: list[CardResult]`.
- Fake hardware contract (`FAKE_HW_DIR`):
  - `cards`: the serial list. Each `ykman list --serials` prints the next serial, makes it `current` and counts in `list_count`.
  - `fail_at`: fails the named operation once. Names: `reset`, `set-retries`, `set-touch`, `change-pin`, `edit-card`, `keytocard1|2|3`, `sign`, `decrypt`.
  - `calls.log`: one line per call. ykman `-a` values are replaced by `<pin>`.
  - `card-<serial>.json`: slot fingerprints.
  - `keys-<serial>/`: a copy of the GnuPG home `keytocard` ran in, used for fake sign/decrypt.
  - `change-pin.txt` / `edit-card.txt`: the stdin scripts received.

- [ ] **Step 1: Write the fake binaries**

`recovery/tests/fakebin/gpg`:

```python
#!/usr/bin/env python3
"""Test double for gpg: card operations are simulated, everything else runs the real gpg."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ARGS = sys.argv[1:]
REAL = os.environ["REAL_GPG"]
STATE = Path(os.environ["FAKE_HW_DIR"])


def log(kind):
    with open(STATE / "calls.log", "a", encoding="utf-8") as f:
        f.write(kind + "\n")


def maybe_fail(kind):
    marker, wanted = STATE / "failed_once", STATE / "fail_at"
    if wanted.exists() and wanted.read_text().strip() == kind and not marker.exists():
        marker.touch()
        sys.stderr.write(f"fake failure at {kind}\n")
        sys.exit(2)


def current_card():
    current = STATE / "current"
    serial = current.read_text().strip() if current.exists() else ""
    path = STATE / f"card-{serial}.json"
    data = json.loads(path.read_text()) if path.exists() else {"fpr": ["", "", ""]}
    return serial, path, data


def main():
    if "--card-status" in ARGS:
        log("card-status")
        serial, _, data = current_card()
        print(f"serial:{serial}:")
        print("fpr:" + ":".join(data["fpr"]) + ":")
        return 0
    if "--change-pin" in ARGS or "--edit-card" in ARGS or "--card-edit" in ARGS:
        kind = "change-pin" if "--change-pin" in ARGS else "edit-card"
        script = sys.stdin.read()
        log(kind)
        with open(STATE / f"{kind}.txt", "a", encoding="utf-8") as f:
            f.write(script)
        maybe_fail(kind)
        return 0
    if "--edit-key" in ARGS:
        script = sys.stdin.read()
        lines = script.splitlines()
        if "keytocard" not in lines:
            return subprocess.run([REAL, *ARGS], input=script, text=True, check=False).returncode
        slot = int(lines[lines.index("keytocard") + 1])
        log(f"keytocard{slot}")
        maybe_fail(f"keytocard{slot}")
        serial, path, data = current_card()
        data["fpr"][slot - 1] = lines[0].split()[1].upper()
        path.write_text(json.dumps(data))
        keys = STATE / f"keys-{serial}"
        if not keys.exists():
            shutil.copytree(os.environ["GNUPGHOME"], keys, ignore=shutil.ignore_patterns("S.*", "*.lock"))
        return 0
    if "--detach-sign" in ARGS or "--decrypt" in ARGS:
        kind = "sign" if "--detach-sign" in ARGS else "decrypt"
        sys.stdin.read()
        log(kind)
        maybe_fail(kind)
        serial, _, _ = current_card()
        env = {**os.environ, "GNUPGHOME": str(STATE / f"keys-{serial}")}
        return subprocess.run([REAL, *ARGS], input=os.environ["FAKE_CERTIFY_PASS"] + "\n", text=True,
                              env=env, check=False).returncode
    os.execv(REAL, [REAL, *ARGS])


if __name__ == "__main__":
    sys.exit(main())
```

`recovery/tests/fakebin/ykman`:

```python
#!/usr/bin/env python3
"""Test double for ykman: records calls; `list --serials` hands out the next serial from `cards`."""

import json
import os
import sys
from pathlib import Path

STATE = Path(os.environ["FAKE_HW_DIR"])


def main():
    args = sys.argv[1:]
    if "-a" in args:
        args[args.index("-a") + 1] = "<pin>"
    with open(STATE / "calls.log", "a", encoding="utf-8") as f:
        f.write("ykman " + " ".join(args) + "\n")
    if args[:2] == ["list", "--serials"]:
        cards = (STATE / "cards").read_text().split()
        counter = STATE / "list_count"
        n = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(n + 1))
        if n < len(cards):
            (STATE / "current").write_text(cards[n])
            print(cards[n])
        return 0
    serial = args[args.index("--device") + 1] if "--device" in args else ""
    current = STATE / "current"
    if serial != (current.read_text().strip() if current.exists() else ""):
        sys.stderr.write("fake ykman: wrong device\n")
        return 1
    for kind in ("reset", "set-retries", "set-touch"):
        if kind in args:
            wanted, marker = STATE / "fail_at", STATE / "failed_once"
            if wanted.exists() and wanted.read_text().strip() == kind and not marker.exists():
                marker.touch()
                sys.stderr.write(f"fake failure at {kind}\n")
                return 2
            if kind == "reset":
                (STATE / f"card-{serial}.json").write_text(json.dumps({"fpr": ["", "", ""]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`recovery/tests/fakebin/gpg-connect-agent`:

```python
#!/usr/bin/env python3
"""Test double for gpg-connect-agent: records the commands and answers OK."""

import os
import sys
from pathlib import Path

with open(Path(os.environ["FAKE_HW_DIR"]) / "calls.log", "a", encoding="utf-8") as f:
    f.write("gpg-connect-agent " + " | ".join(sys.argv[1:]) + "\n")
print("OK")
```

Run: `chmod +x recovery/tests/fakebin/*`

- [ ] **Step 2: Write the failing fake-hardware tests**

`recovery/tests/test_fake_hardware.py`:

```python
import json
import unittest
from pathlib import Path

from tests.helpers import FIXTURES, PASSPHRASE, _runtime_base, fake_hardware, fixture_info, run_impl, short_tempdir

S1, S2 = "11111111", "22222222"


def tool_homes():
    return set(_runtime_base().glob("ykr-*"))


class FakeHardwareTests:
    """Full migrations against fake card operations; shared by both implementations."""

    IMPL = ""

    def migrate(self, profile, extra, stdin, serials=(S1,), fail_at=""):
        env = fake_hardware(self, list(serials), fail_at)
        out = short_tempdir(self, "out")
        result = run_impl(self.IMPL, ["--backup", str(FIXTURES / profile / "export"), "--out", str(out), *extra],
                          stdin, env)
        state = Path(env["FAKE_HW_DIR"])
        calls = (state / "calls.log").read_text() if (state / "calls.log").exists() else ""
        return result, out, calls, state

    def card_fprs(self, state, serial):
        return json.loads((state / f"card-{serial}.json").read_text())["fpr"]

    def test_clone_one_card(self):
        info = fixture_info("ed25519")
        result, out, calls, state = self.migrate("ed25519", [], f"{PASSPHRASE}\ny\n\nRESET {S1}\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.card_fprs(state, S1), [info["FPR_S"], info["FPR_E"], info["FPR_A"]])
        self.assertIn(f"ykman --device {S1} openpgp reset -f", calls)
        self.assertIn(f"ykman --device {S1} openpgp keys set-touch aut on -f -a <pin>", calls)
        self.assertIn("decrypt", calls)
        self.assertTrue((out / f"ssh-{info['KEY_ID']}.pub").is_file())
        self.assertNotIn(PASSPHRASE, result.stderr)
        self.assertIn("login\nyk.", (state / "edit-card.txt").read_text())

    def test_two_cards_same_pins(self):
        stdin = f"{PASSPHRASE}\ny\n\nRESET {S1}\n\nRESET {S2}\n"
        result, _, _, state = self.migrate("ed25519", ["--cards", "2", "--same-pins"], stdin, serials=(S1, S2))
        self.assertEqual(result.returncode, 0, result.stderr)
        scripts = (state / "change-pin.txt").read_text().split("q\n")
        admin_pins = {s.split("\n")[2] for s in scripts if s.startswith("3\n")}
        self.assertEqual(len(admin_pins), 1)
        self.assertRegex(admin_pins.pop(), r"^\d{8}$")

    def test_entered_pins_reprompt_when_short(self):
        stdin = f"{PASSPHRASE}\ny\n123\n11223344\n11223344\n654321\n654321\n\nRESET {S1}\n"
        result, _, _, state = self.migrate("ed25519", ["--pins", "enter"], stdin)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((state / "change-pin.txt").read_text(),
                         "3\n12345678\n11223344\n11223344\nq\n1\n123456\n654321\n654321\nq\n")

    def test_retry_after_failure(self):
        stdin = f"{PASSPHRASE}\ny\n\nRESET {S1}\nr\n"
        result, _, calls, _ = self.migrate("ed25519", [], stdin, fail_at="keytocard2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls.count(f"ykman --device {S1} openpgp reset -f"), 2)

    def test_skip_after_failure(self):
        result, _, _, _ = self.migrate("ed25519", [], f"{PASSPHRASE}\ny\n\nRESET {S1}\ns\n", fail_at="sign")
        self.assertEqual(result.returncode, 3)

    def test_reset_not_confirmed(self):
        result, _, calls, _ = self.migrate("ed25519", [], f"{PASSPHRASE}\ny\n\nreset please\n")
        self.assertEqual(result.returncode, 3)
        self.assertNotIn("reset -f", calls)

    def test_wrong_passphrase_three_times(self):
        result, _, _, _ = self.migrate("ed25519", [], "a\nb\nc\n")
        self.assertEqual(result.returncode, 2)

    def test_declined_key_confirmation(self):
        result, _, calls, _ = self.migrate("ed25519", [], f"{PASSPHRASE}\nn\n")
        self.assertEqual(result.returncode, 130)
        self.assertEqual(calls, "")

    def test_no_card_reprompts_then_eof_aborts(self):
        result, _, _, _ = self.migrate("ed25519", [], f"{PASSPHRASE}\ny\n\n", serials=())
        self.assertEqual(result.returncode, 130)
        self.assertIn("expected exactly one YubiKey, found 0", result.stderr)

    def test_abort_cleans_up_homes(self):
        before = tool_homes()
        result, _, _, _ = self.migrate("ed25519", [], f"{PASSPHRASE}\ny\n\n")
        self.assertEqual(result.returncode, 130)
        self.assertEqual(tool_homes() - before, set())

    def test_renew_requires_saved_backup_confirmation(self):
        info = fixture_info("expired")
        stdin = f"{PASSPHRASE}\ny\nn\ny\n\nRESET {S1}\n"
        result, out, _, state = self.migrate("expired", ["--mode", "renew"], stdin)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("copy the new backup before writing cards", result.stderr)
        self.assertTrue((out / f"secret-{info['KEY_ID']}-Certify.key").is_file())
        self.assertEqual(self.card_fprs(state, S1), [info["FPR_S"], info["FPR_E"], info["FPR_A"]])

    def test_rotate_writes_new_subkeys(self):
        info = fixture_info("rsa4096")
        stdin = f"{PASSPHRASE}\ny\ny\n\nRESET {S1}\n"
        result, _, _, state = self.migrate("rsa4096", ["--mode", "rotate", "--algo", "ed25519", "--revoke-old"], stdin)
        self.assertEqual(result.returncode, 0, result.stderr)
        fprs = self.card_fprs(state, S1)
        self.assertTrue(all(fprs))
        self.assertFalse(set(fprs) & {info["FPR_S"], info["FPR_E"], info["FPR_A"]})


class PythonFakeHardwareTests(FakeHardwareTests, unittest.TestCase):
    IMPL = "python"


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd recovery && python3 -m unittest tests.test_fake_hardware -v`
Expected: most tests fail with stderr containing `AttributeError: 'Session' object has no attribute 'execute'`. `test_wrong_passphrase_three_times` and `test_declined_key_confirmation` already pass.

- [ ] **Step 4: Implement card writing**

In `recovery/ykrecover/migrate.py`, extend the imports:

```python
import secrets
import subprocess

from . import EXIT_BACKUP, EXIT_CARD, EXIT_INTERRUPT, EXIT_OK, EXIT_USAGE, Aborted, RecoveryError
from .plan import CARDHOME, STAGING, VERIFYHOME, format_plan, public_tokens, substitute
```

Add these methods to `Session` (after `apply_mode`):

```python
    def execute(self) -> int:
        """Everything after the passphrase is unlocked and the key confirmed."""
        o = self.opts
        o.out.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.apply_mode()
        if o.mode != "clone":
            while not self.ui.confirm(f"New backup written to {o.out}. Saved it to offline storage?"):
                self.ui.warn("copy the new backup before writing cards")
        self.assign_pins()
        self.write_cards()
        return self.finish()

    def assign_pins(self) -> None:
        o = self.opts
        self.results = [CardResult(i) for i in range(1, o.cards + 1)]
        shared = None
        for r in self.results:
            if not (o.same_pins and shared):
                if o.pins == "generate":
                    shared = card.generate_pins()
                else:
                    label = "all cards" if o.same_pins else f"card {r.index}"
                    shared = (self._ask_pin(f"Admin PIN ({label})", 8), self._ask_pin(f"User PIN ({label})", 6))
            r.admin_pin, r.user_pin = shared
            self.logins[r.index] = o.login or card.random_login()

    def _ask_pin(self, label: str, minimum: int) -> str:
        while True:
            pin = self.ui.ask_secret(f"{label}:")
            if not minimum <= len(pin) <= 127:
                self.ui.warn(f"must be {minimum} to 127 characters")
                continue
            if self.ui.ask_secret(f"Repeat {label}:") != pin:
                self.ui.warn("entries differ")
                continue
            return pin

    def write_cards(self) -> None:
        # Release the card from the user's own scdaemon so ykman can open it.
        subprocess.run(["gpgconf", "--kill", "scdaemon"], capture_output=True, check=False)
        done = set()
        for r in self.results:
            r.serial = self._wait_for_card(r.index, done)
            done.add(r.serial)
            if not self.ui.confirm_reset(r.serial):
                r.status, r.detail = "SKIPPED", "reset not confirmed"
                continue
            while True:
                try:
                    self._write_card(r)
                except StepError as err:
                    choice = self.ui.card_failed(r.index, r.serial, err.shown, str(err))
                    if choice == "retry":
                        continue
                    r.status, r.detail = "FAILED", err.shown
                    if choice == "abort":
                        raise Aborted() from err
                    break
                r.status, r.detail = "OK", ""
                self.ui.progress(r.index, f"{r.serial}: OK")
                break

    def _wait_for_card(self, index: int, done: set) -> str:
        while True:
            self.ui.wait_for_card(index, self.opts.cards)
            listing = subprocess.run(["ykman", "list", "--serials"], capture_output=True, text=True, check=False)
            serials = card.parse_serials(listing.stdout)
            if len(serials) != 1:
                self.ui.warn(f"expected exactly one YubiKey, found {len(serials)}")
            elif serials[0] in done:
                self.ui.warn(f"card {serials[0]} was already written in this session")
            else:
                return serials[0]

    def _write_card(self, r: CardResult) -> None:
        cardhome = self.staging.clone("card")
        verifyhome = Home.create("verify")
        try:
            (verifyhome.path / "test.txt").write_text(secrets.token_hex(32) + "\n")
            surname, given = card.split_name(self.opts.name)
            tokens = {**self._tokens(), f"<SERIAL{r.index}>": r.serial, "<ADMIN_PIN>": r.admin_pin,
                      "<USER_PIN>": r.user_pin, "<LOGIN>": self.logins[r.index], "<SURNAME>": surname,
                      "<GIVEN>": given, "<URL>": self.opts.url, "<CARDHOME>": str(cardhome.path),
                      "<VERIFYHOME>": str(verifyhome.path)}
            homes = {STAGING: self.staging, CARDHOME: cardhome, VERIFYHOME: verifyhome}
            for step in self.card_plan(r.index, self.targets):
                self.ui.progress(r.index, f"{r.serial}: {substitute(' '.join(step.argv), public_tokens(tokens))}")
                self._check(step, self._exec(step, homes, tokens), verifyhome)
        finally:
            cardhome.destroy()
            verifyhome.destroy()

    def _check(self, step, output: str, verifyhome: Home) -> None:
        if step.check == "cardstatus":
            want = [self.targets[u].upper() for u in modes.USAGES]
            got = card.parse_card_fprs(output)
            if got != want:
                raise StepError("card fingerprints", f"card has {got}, expected {want}")
        elif step.check == "roundtrip":
            if (verifyhome.path / "test.out").read_bytes() != (verifyhome.path / "test.txt").read_bytes():
                raise StepError("decrypt round-trip", "decrypted text differs from the original")

    def finish(self) -> int:
        b, o = self.bundle, self.opts
        for step in modes.finish_steps(b.key_fp, b.key_id, self.targets):
            try:
                self._exec(step, {STAGING: self.staging}, self._tokens())
            except StepError as err:
                self.ui.warn(f"export failed: {err}")
        self.ui.show_pins([(r.serial, r.admin_pin, r.user_pin) for r in self.results if r.status == "OK"])
        summary = [f"card {r.index} {r.serial or '-'} {r.status} {r.detail}".rstrip() for r in self.results]
        self.ui.info("Summary:\n" + "\n".join(summary))
        public = o.out / f"public-{b.key_id}-{datetime.date.today().isoformat()}.asc"
        self.ui.info(f"On your daily machine run: yubikey-recovery --cli update-stubs --import {public} "
                     f"--cards {o.cards}")
        return EXIT_OK if all(r.status == "OK" for r in self.results) else EXIT_CARD
```

Remove now-unused names from the import line if any linter complains (`EXIT_USAGE` is still used by `validate_options`).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd recovery && python3 -m unittest tests.test_fake_hardware -v`
Expected: `OK` (12 tests).

- [ ] **Step 6: Run the whole suite**

Run: `cd recovery && python3 -m unittest discover -s tests -t . -v`
Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
git add recovery/ykrecover/migrate.py recovery/tests/fakebin recovery/tests/test_fake_hardware.py
git commit -m "recovery: write and verify cards, with fake hardware tests"
```

---

### Task 7: update-stubs (Python)

**Files:**

- Create: `recovery/ykrecover/stubs.py`
- Test: `recovery/tests/test_stubs.py`

**Interfaces:**

- Consumes: `CliUI` protocol, `Aborted`, `RecoveryError`.
- Produces: `user_home() -> Path`, `find_stubs(home, serial) -> list[Path]` (matches `Token:` lines only), `update_stubs(ui, import_file, remove_serials, cards) -> int`.
- CLI contract: `update-stubs [--import FILE] [--remove-serial S ...] [--cards N]`. Prompts: `Move <n> stub(s) for card <S> out of the keyring?…[y/N]`, then `Insert card <i> of <N> and press Enter` per card. It operates on the inherited `GNUPGHOME` or `~/.gnupg`.

- [ ] **Step 1: Write the failing tests**

`recovery/tests/test_stubs.py`:

```python
import unittest
from pathlib import Path

from tests.helpers import FIXTURES, fake_hardware, fixture_info, run_impl, short_tempdir

OLD, OTHER = "22222222", "33333333"


def stub(serial, slot):
    return f"Key: (shadowed-private-key (ecc (curve Ed25519)))\nToken: D2760001240103040006{serial}0000 OPENPGP.{slot} - - -\n"


class StubsTests:
    IMPL = ""

    def setUp(self):
        self.home = short_tempdir(self, "home")
        self.home.chmod(0o700)
        keys = self.home / "private-keys-v1.d"
        keys.mkdir(mode=0o700)
        (keys / "AAAA.key").write_text(stub(OLD, 1))
        (keys / "BBBB.key").write_text(stub(OLD, 2))
        (keys / "CCCC.key").write_text(stub(OTHER, 1))
        self.env = {**fake_hardware(self, ["44444444"]), "GNUPGHOME": str(self.home)}

    def run_stubs(self, args, stdin):
        return run_impl(self.IMPL, ["update-stubs", *args], stdin, self.env)

    def test_moves_old_stubs_and_learns_new_card(self):
        result = self.run_stubs(["--remove-serial", OLD], "y\n\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        remaining = sorted(p.name for p in (self.home / "private-keys-v1.d").iterdir())
        self.assertEqual(remaining, ["CCCC.key"])
        (backup_dir,) = self.home.glob("stub-backup-*")
        self.assertEqual(sorted(p.name for p in backup_dir.iterdir()), ["AAAA.key", "BBBB.key"])
        calls = (Path(self.env["FAKE_HW_DIR"]) / "calls.log").read_text()
        self.assertIn("gpg-connect-agent scd serialno | learn --force | /bye", calls)

    def test_declining_keeps_stubs(self):
        result = self.run_stubs(["--remove-serial", OLD], "n\n\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list((self.home / "private-keys-v1.d").iterdir())), 3)

    def test_import_public_key(self):
        info = fixture_info("ed25519")
        public = FIXTURES / "ed25519" / "export" / f"public-{info['KEY_ID']}-2026-10-11.asc"
        result = self.run_stubs(["--import", str(public)], "\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_eof_aborts(self):
        self.assertEqual(self.run_stubs([], "").returncode, 130)


class PythonStubsTests(StubsTests, unittest.TestCase):
    IMPL = "python"


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd recovery && python3 -m unittest tests.test_stubs -v`
Expected: FAIL, stderr `ModuleNotFoundError: No module named 'ykrecover.stubs'`.

- [ ] **Step 3: Implement `stubs.py`**

`recovery/ykrecover/stubs.py`:

```python
"""update-stubs: point the daily keyring at the new cards.

This is the only part of the toolset that touches the user's own GNUPGHOME.
"""

import datetime
import os
import shutil
import subprocess
from pathlib import Path

from . import EXIT_INTERRUPT, EXIT_OK, EXIT_USAGE, Aborted, RecoveryError


def user_home() -> Path:
    return Path(os.environ.get("GNUPGHOME") or Path.home() / ".gnupg")


def find_stubs(home: Path, serial: str) -> list:
    """Shadowed-key files whose Token line names the card serial."""
    found = []
    for path in sorted((home / "private-keys-v1.d").glob("*.key")):
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split()
            if len(parts) > 1 and parts[0] == "Token:" and serial in parts[1]:
                found.append(path)
                break
    return found


def update_stubs(ui, import_file=None, remove_serials=(), cards=1) -> int:
    home = user_home()
    try:
        missing = [t for t in ("gpg", "gpg-connect-agent") if shutil.which(t) is None]
        if missing:
            raise RecoveryError(f"missing tools: {' '.join(missing)}", EXIT_USAGE)
        if import_file:
            result = subprocess.run(["gpg", "--batch", "--import", str(import_file)],
                                    capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise RecoveryError(f"import failed: {result.stderr.strip()}", EXIT_USAGE)
        for serial in remove_serials:
            stubs = find_stubs(home, serial)
            if not stubs:
                ui.info(f"no stubs reference card {serial}")
                continue
            names = "".join(f"\n  {p.name}" for p in stubs)
            if not ui.confirm(f"Move {len(stubs)} stub(s) for card {serial} out of the keyring?{names}\n"):
                continue
            dest = home / f"stub-backup-{datetime.datetime.now():%Y%m%d-%H%M%S}"
            dest.mkdir(mode=0o700, exist_ok=True)
            for path in stubs:
                shutil.move(str(path), str(dest / path.name))
            ui.info(f"moved to {dest}")
        for index in range(1, cards + 1):
            ui.wait_for_card(index, cards)
            learn = subprocess.run(["gpg-connect-agent", "scd serialno", "learn --force", "/bye"],
                                   capture_output=True, text=True, check=False)
            if learn.returncode != 0:
                ui.warn(learn.stderr.strip())
            status = subprocess.run(["gpg", "--batch", "--card-status"], capture_output=True, text=True, check=False)
            ui.info(status.stdout.strip())
        return EXIT_OK
    except RecoveryError as err:
        ui.error(str(err))
        return err.code
    except (Aborted, KeyboardInterrupt):
        ui.error("aborted")
        return EXIT_INTERRUPT
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd recovery && python3 -m unittest tests.test_stubs -v`
Expected: `OK` (4 tests).

- [ ] **Step 5: Commit**

```bash
git add recovery/ykrecover/stubs.py recovery/tests/test_stubs.py
git commit -m "recovery: update-stubs for the daily keyring"
```

---

### Task 8: Tkinter wizard

**Files:**

- Create: `recovery/ykrecover/gui.py`
- Test: `recovery/tests/test_gui.py`

**Interfaces:**

- Consumes: `Session` (all methods), `Options`, `backup.detect`, `backup.AmbiguousKey`, `card.TOUCH_POLICIES`.
- Produces: `TkUI(app)` implementing the UI protocol from the worker thread, `App(root)` (8 pages), `main() -> int`.

- [ ] **Step 1: Write the failing smoke test**

`recovery/tests/test_gui.py`:

```python
import os
import unittest

try:
    import tkinter as tk
except ImportError:  # python3-tk not installed
    tk = None


@unittest.skipIf(tk is None or not os.environ.get("DISPLAY"), "needs tkinter and a display")
class GuiSmokeTests(unittest.TestCase):
    def test_wizard_builds_and_closes(self):
        from ykrecover.gui import App
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = App(root)
        app.alert = lambda text: None  # keep the test headless-safe: no modal error dialog
        root.update()
        self.assertEqual(app.page, 0)
        self.assertEqual(len(app.pages), 8)
        app.v["folder"].set("/nonexistent")
        app.show(1)
        root.update()
        self.assertFalse(app.leave_backup())  # nothing detected → stays on the page

    def test_tkui_marshals_calls(self):
        from ykrecover.gui import App
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = App(root)
        app.ui.progress(1, "hello")  # queued, not executed on this thread
        self.assertFalse(app.tasks.empty())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd recovery && python3 -m unittest tests.test_gui -v`
Expected: ERROR `No module named 'ykrecover.gui'` (or `skipped` without a display; in that case also run `python3 -c "import ykrecover.gui"` from `recovery/` and expect `ModuleNotFoundError`).

- [ ] **Step 3: Implement `gui.py`**

`recovery/ykrecover/gui.py`:

```python
"""Tkinter wizard over migrate.Session.

Card work runs on a worker thread; every UI call it makes is queued to the Tk main loop,
which owns all widgets and dialogs.
"""

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import EXIT_INTERRUPT, Aborted, RecoveryError
from . import backup as backup_mod
from .card import TOUCH_POLICIES
from .migrate import Options, Session

TITLE = "YubiKey recovery"
METADATA_WARNING = ("OpenPGP card metadata (name, login, URL) can be read by anyone holding the card. "
                    "Leave the name blank unless you want it for lost-and-found.")
MODE_HELP = ("clone: write the backed-up subkeys as they are.\n"
             "renew: extend their expiry first (needs the Certify key).\n"
             "rotate: create new subkeys (needs the Certify key). Choose this if the old cards may be "
             "lost or compromised.")


class _Collector:
    def __init__(self, sink):
        self.sink = sink

    def warn(self, msg):
        self.sink.append(msg)


class TkUI:
    """migrate.Session's UI, safe to call from the worker thread."""

    def __init__(self, app):
        self.app = app

    def _ask(self, fn):
        done, box = threading.Event(), {}

        def task():
            try:
                box["value"] = fn()
            finally:
                done.set()

        self.app.tasks.put(task)
        done.wait()
        return box.get("value")

    def output(self, text):
        self.app.post(lambda: self.app.log(text))

    def info(self, msg):
        self.app.post(lambda: self.app.log(msg))

    def warn(self, msg):
        self.app.post(lambda: self.app.log(f"warning: {msg}"))

    def error(self, msg):
        self.app.post(lambda: self.app.log(f"error: {msg}"))

    def ask_secret(self, prompt):
        value = self._ask(lambda: simpledialog.askstring(TITLE, prompt, show="*", parent=self.app.root))
        if value is None:
            raise Aborted()
        return value

    def confirm(self, prompt):
        return bool(self._ask(lambda: messagebox.askyesno(TITLE, prompt, parent=self.app.root)))

    def confirm_reset(self, serial):
        typed = self._ask(lambda: simpledialog.askstring(
            "Erase card", f"All OpenPGP data on card {serial} will be erased.\nType RESET {serial} to continue:",
            parent=self.app.root))
        return typed == f"RESET {serial}"

    def wait_for_card(self, index, total):
        if not self._ask(lambda: messagebox.askokcancel(
                "Insert card", f"Insert card {index} of {total}. Only that YubiKey may be connected.",
                parent=self.app.root)):
            raise Aborted()

    def card_failed(self, index, serial, step, error):
        answer = self._ask(lambda: messagebox.askyesnocancel(
            "Card failed", f"Card {serial} failed at:\n{step}\n\n{error}\n\nYes: retry   No: skip   Cancel: abort",
            parent=self.app.root))
        return {True: "retry", False: "skip", None: "abort"}[answer]

    def progress(self, index, status):
        self.app.post(lambda: self.app.set_card(index, status))

    def show_pins(self, pins):
        self.app.post(lambda: self.app.keep_pins(pins))


class App:
    def __init__(self, root):
        self.root = root
        root.title(TITLE)
        root.minsize(760, 560)
        self.alert = lambda text: messagebox.showerror(TITLE, text, parent=self.root)
        self.tasks = queue.Queue()
        self.ui = TkUI(self)
        self.session = None
        self.candidates = []
        self.failures = 0
        self.pins = []
        self.worker = None
        self.table = None
        self.logbox = None
        self.v = {
            "folder": tk.StringVar(), "passphrase": tk.StringVar(), "mode": tk.StringVar(value="clone"),
            "expire": tk.StringVar(value="2y"), "algo": tk.StringVar(value=""), "revoke": tk.BooleanVar(),
            "cards": tk.StringVar(value="1"), "pins": tk.StringVar(value="generate"), "same": tk.BooleanVar(),
            "retries": tk.StringVar(value="5,5,5"), "sig": tk.StringVar(value="on"),
            "dec": tk.StringVar(value="on"), "aut": tk.StringVar(value="on"), "name": tk.StringVar(),
            "login": tk.StringVar(), "url": tk.StringVar(), "out": tk.StringVar(),
        }
        self.body = ttk.Frame(root, padding=12)
        self.body.pack(fill="both", expand=True)
        nav = ttk.Frame(root, padding=(12, 0, 12, 12))
        nav.pack(fill="x")
        self.back_btn = ttk.Button(nav, text="Back", command=lambda: self.show(self.page - 1))
        self.back_btn.pack(side="left")
        self.next_btn = ttk.Button(nav, text="Next", command=self.next)
        self.next_btn.pack(side="right")
        self.pages = [
            (self.build_preflight, None), (self.build_backup, self.leave_backup), (self.build_key, None),
            (self.build_mode, self.leave_mode), (self.build_settings, self.leave_settings),
            (self.build_output, self.leave_output), (self.build_cards, None), (self.build_summary, None),
        ]
        self.page = 0
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)
        self.show(0)

    # plumbing
    def post(self, fn):
        self.tasks.put(fn)

    def poll(self):
        while True:
            try:
                task = self.tasks.get_nowait()
            except queue.Empty:
                break
            task()
        self.root.after(100, self.poll)

    def show(self, page):
        for child in self.body.winfo_children():
            child.destroy()
        self.page = page
        self.back_btn.state(["!disabled"] if 0 < page < 6 else ["disabled"])
        self.next_btn.state(["!disabled"] if page < 6 else ["disabled"])
        self.pages[page][0]()

    def next(self):
        leave = self.pages[self.page][1]
        if leave is None or leave():
            self.show(self.page + 1)

    def heading(self, text, detail=""):
        ttk.Label(self.body, text=text, font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
        if detail:
            ttk.Label(self.body, text=detail, wraplength=700, justify="left").pack(anchor="w", pady=(4, 12))

    def row(self, label, make):
        frame = ttk.Frame(self.body)
        frame.pack(fill="x", pady=2)
        ttk.Label(frame, text=label, width=26).pack(side="left")
        widget = make(frame)
        widget.pack(side="left", fill="x", expand=True)
        return widget

    def readonly_text(self, content, height):
        text = tk.Text(self.body, height=height, wrap="none")
        text.insert("end", content)
        text.config(state="disabled")
        text.pack(fill="both", expand=True)

    def log(self, text):
        if self.logbox is not None and self.logbox.winfo_exists():
            self.logbox.insert("end", text.rstrip("\n") + "\n")
            self.logbox.see("end")

    def set_card(self, index, status):
        if self.table is not None and self.table.winfo_exists() and self.table.exists(str(index)):
            self.table.item(str(index), values=(status,))

    def keep_pins(self, pins):
        self.pins = list(pins)

    # pages
    def build_preflight(self):
        self.heading("1. Preflight", "Checks tools and the environment. An air-gapped system is recommended.")
        warnings = []
        try:
            Session(Options(backup=Path(".")), _Collector(warnings)).preflight()
            lines = ["All required tools found.", *(f"warning: {w}" for w in warnings)]
        except RecoveryError as err:
            lines = [str(err)]
            self.next_btn.state(["disabled"])
        self.readonly_text("\n".join(lines), 8)

    def build_backup(self):
        self.heading("2. Backup", "Choose the folder holding your backup, for example the mounted encrypted "
                                  "USB volume. It is only read, never changed.")
        frame = ttk.Frame(self.body)
        frame.pack(fill="x")
        ttk.Entry(frame, textvariable=self.v["folder"]).pack(side="left", fill="x", expand=True)
        ttk.Button(frame, text="Browse…", command=self.browse_backup).pack(side="left", padx=4)
        ttk.Button(frame, text="Detect", command=self.refresh_candidates).pack(side="left")
        self.cand_list = tk.Listbox(self.body, height=6, exportselection=False)
        self.cand_list.pack(fill="x", pady=8)
        self.refresh_candidates()
        self.row("Certify key passphrase", lambda f: ttk.Entry(f, textvariable=self.v["passphrase"], show="*"))

    def browse_backup(self):
        folder = filedialog.askdirectory(parent=self.root, mustexist=True)
        if folder:
            self.v["folder"].set(folder)
            self.refresh_candidates()

    def refresh_candidates(self):
        self.cand_list.delete(0, "end")
        self.candidates = []
        folder = self.v["folder"].get()
        if not folder:
            return
        try:
            self.candidates = backup_mod.detect(Path(folder))
        except RecoveryError as err:
            self.cand_list.insert("end", str(err))
            return
        for c in self.candidates:
            self.cand_list.insert("end", f"{c.kind}: {c.path}")
        if len(self.candidates) == 1:
            self.cand_list.selection_set(0)

    def leave_backup(self):
        chosen = self.cand_list.curselection()
        if not chosen or chosen[0] >= len(self.candidates):
            self.alert("Select a detected backup.")
            return False
        candidate = self.candidates[chosen[0]]
        if self.session:
            self.session.cleanup()
        self.session = Session(Options(backup=candidate.path, source=candidate.kind), self.ui)
        while True:
            try:
                self.session.load()
                break
            except backup_mod.AmbiguousKey as err:
                key = simpledialog.askstring(TITLE, "Several keys found:\n" + "\n".join(b.key_fp for b in err.bundles)
                                             + "\n\nEnter the fingerprint to use:", parent=self.root)
                if not key:
                    return False
                self.session.opts.key = key
            except RecoveryError as err:
                self.alert(str(err))
                return False
        if not self.session.unlock(self.v["passphrase"].get()):
            self.failures += 1
            if self.failures >= 3:
                self.alert("Wrong passphrase three times.")
                self.close()
            else:
                self.alert("Wrong passphrase.")
            return False
        self.v["passphrase"].set("")
        return True

    def build_key(self):
        self.heading("3. Confirm key", "The subkeys marked <- card will be written to every new card.")
        self.readonly_text(self.session.describe(), 14)

    def build_mode(self):
        self.heading("4. Mode", MODE_HELP)
        for mode in ("clone", "renew", "rotate"):
            button = ttk.Radiobutton(self.body, text=mode, value=mode, variable=self.v["mode"])
            button.pack(anchor="w")
            if mode != "clone" and not self.session.bundle.has_certify_secret:
                button.state(["disabled"])
        self.row("Expiry (renew, rotate)", lambda f: ttk.Entry(f, textvariable=self.v["expire"]))
        self.row("Algorithm (rotate)", lambda f: ttk.Combobox(
            f, textvariable=self.v["algo"], values=("", "rsa4096", "ed25519"), state="readonly"))
        ttk.Checkbutton(self.body, text="Revoke the old subkeys (rotate)", variable=self.v["revoke"]).pack(anchor="w")

    def leave_mode(self):
        o = self.session.opts
        o.mode = self.v["mode"].get()
        o.expire = self.v["expire"].get().strip() or "2y"
        o.algo = self.v["algo"].get() if o.mode == "rotate" else ""
        o.revoke_old = self.v["revoke"].get() and o.mode == "rotate"
        try:
            self.session.validate_bundle()
            return True
        except RecoveryError as err:
            self.alert(str(err))
            return False

    def build_settings(self):
        self.heading("5. Card settings", METADATA_WARNING)
        self.row("Number of cards", lambda f: ttk.Spinbox(f, from_=1, to=10, textvariable=self.v["cards"], width=5))
        pins = ttk.Frame(self.body)
        pins.pack(fill="x", pady=2)
        ttk.Label(pins, text="PINs", width=26).pack(side="left")
        ttk.Radiobutton(pins, text="generate", value="generate", variable=self.v["pins"]).pack(side="left")
        ttk.Radiobutton(pins, text="enter", value="enter", variable=self.v["pins"]).pack(side="left")
        ttk.Checkbutton(pins, text="same PINs on every card", variable=self.v["same"]).pack(side="left", padx=12)
        self.row("Retries (user,reset,admin)", lambda f: ttk.Entry(f, textvariable=self.v["retries"]))
        for ref, label in (("sig", "Touch: sign"), ("dec", "Touch: decrypt"), ("aut", "Touch: authenticate")):
            self.row(label, lambda f, ref=ref: ttk.Combobox(f, textvariable=self.v[ref], values=TOUCH_POLICIES,
                                                            state="readonly"))
        self.row("Cardholder name", lambda f: ttk.Entry(f, textvariable=self.v["name"]))
        self.row("Login (blank = random)", lambda f: ttk.Entry(f, textvariable=self.v["login"]))
        self.row("Public key URL", lambda f: ttk.Entry(f, textvariable=self.v["url"]))

    def leave_settings(self):
        o = self.session.opts
        cards = self.v["cards"].get().strip()
        o.cards = int(cards) if cards.isdigit() else 0
        o.pins, o.same_pins = self.v["pins"].get(), self.v["same"].get()
        o.retries = tuple(self.v["retries"].get().split(","))
        o.touch = {ref: self.v[ref].get() for ref in ("sig", "dec", "aut")}
        o.name, o.login, o.url = (self.v[k].get().strip() for k in ("name", "login", "url"))
        return True

    def build_output(self):
        self.heading("6. Output folder", "New backups (renew, rotate), the public key and the SSH key are written "
                                         "here. Use an empty folder outside the backup folder.")
        frame = ttk.Frame(self.body)
        frame.pack(fill="x")
        ttk.Entry(frame, textvariable=self.v["out"]).pack(side="left", fill="x", expand=True)
        ttk.Button(frame, text="Browse…", command=lambda: self.v["out"].set(
            filedialog.askdirectory(parent=self.root) or self.v["out"].get())).pack(side="left", padx=4)

    def leave_output(self):
        o = self.session.opts
        o.out = Path(self.v["out"].get()) if self.v["out"].get().strip() else None
        try:
            self.session.validate_options()
            return True
        except RecoveryError as err:
            self.alert(str(err))
            return False

    def build_cards(self):
        self.heading("7. Write cards", "Press Start. You will be asked to insert each card and confirm its reset.")
        self.table = ttk.Treeview(self.body, columns=("status",), height=6)
        self.table.heading("#0", text="Card")
        self.table.heading("status", text="Status")
        self.table.column("status", width=560)
        self.table.pack(fill="x")
        for i in range(1, self.session.opts.cards + 1):
            self.table.insert("", "end", iid=str(i), text=f"card {i}", values=("waiting",))
        self.logbox = tk.Text(self.body, height=12, wrap="word")
        self.logbox.pack(fill="both", expand=True, pady=8)
        self.start_btn = ttk.Button(self.body, text="Start", command=self.start)
        self.start_btn.pack(anchor="e")

    def start(self):
        self.start_btn.state(["disabled"])
        self.worker = threading.Thread(target=self.work, daemon=True)
        self.worker.start()

    def work(self):
        try:
            code = self.session.execute()
        except RecoveryError as err:
            self.ui.error(str(err))
            code = err.code
        except Aborted:
            self.ui.error("aborted")
            code = EXIT_INTERRUPT
        self.post(lambda: self.finished(code))

    def finished(self, code):
        self.log(f"finished with exit code {code}")
        self.next_btn.state(["!disabled"])

    def build_summary(self):
        self.heading("8. Summary")
        o = self.session.opts
        lines = [f"card {r.index}  {r.serial or '-'}  {r.status}  {r.detail}" for r in self.session.results]
        lines += ["", f"Files are in {o.out}.", "On your daily machine run:",
                  "  yubikey-recovery --cli update-stubs --import <public key file> --remove-serial <old serial>"]
        self.readonly_text("\n".join(lines), 12)
        self.pin_btn = ttk.Button(self.body, text="Show PINs (once)", command=self.reveal_pins)
        self.pin_btn.pack(anchor="e", pady=8)

    def reveal_pins(self):
        text = "\n".join(f"Card {s}: Admin PIN {a}   User PIN {u}" for s, a, u in self.pins) or "No card was written."
        messagebox.showinfo("PINs", text, parent=self.root)
        self.pins = []
        self.pin_btn.state(["disabled"])

    def close(self):
        busy = self.worker is not None and self.worker.is_alive()
        if busy and not messagebox.askyesno(TITLE, "Card writing is in progress. Quit anyway?", parent=self.root):
            return
        if self.session:
            self.session.cleanup()
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0
```

- [ ] **Step 4: Run the tests**

Run: `cd recovery && python3 -m unittest tests.test_gui -v`
Expected: `OK` (2 tests), or `skipped 'needs tkinter and a display'` in headless environments. Also run `cd recovery && python3 -c "import ykrecover.gui"` and expect no output.

- [ ] **Step 5: Manual check with fake hardware**

Run from the repo root on a desktop session:

```bash
mkdir -p /tmp/ykhw && printf '11111111\n' > /tmp/ykhw/cards && \
PATH="$PWD/recovery/tests/fakebin:$PATH" FAKE_HW_DIR=/tmp/ykhw REAL_GPG=$(command -v gpg) \
FAKE_CERTIFY_PASS=test-certify-pass PYTHONPATH=recovery python3 -m ykrecover
```

Walk through the wizard:

- **Backup page:** `recovery/tests/fixtures/ed25519/export`, passphrase `test-certify-pass`.
- **Mode:** clone.
- **Cards:** 1.
- **Output folder:** `/tmp/ykout`.
- **Start**, then OK at "Insert card", then type `RESET 11111111` at the reset prompt.

Expected: the card row shows `11111111: OK`, and the summary lists the card as OK with working "Show PINs (once)". Afterwards, run `rm -rf /tmp/ykhw /tmp/ykout`.

- [ ] **Step 6: Commit**

```bash
git add recovery/ykrecover/gui.py recovery/tests/test_gui.py
git commit -m "recovery: Tkinter wizard"
```

---

### Task 9: Bash implementation — options, backup loading, dry-run

**Files:**

- Create: `recovery/recoverCard.sh`
- Modify: `recovery/tests/test_cli_contract.py`, `recovery/tests/test_plan_parity.py` (add bash classes)

**Interfaces:**

- Consumes: the contract (options, exit codes, plan format, golden files) from Task 4.
- Produces: `recoverCard.sh` with the full dry-run path. `run_live` (Task 10) and `update_stubs` (Task 11) are called from `main` and defined later; Task 9 tests never reach them.

- [ ] **Step 1: Add the bash test classes (failing)**

Append to `recovery/tests/test_cli_contract.py` (above the `if __name__` block):

```python
class BashContractTests(ContractTests, unittest.TestCase):
    IMPL = "bash"
```

Append to `recovery/tests/test_plan_parity.py` (above the `if __name__` block):

```python
class BashPlanParityTests(PlanParityTests, unittest.TestCase):
    IMPL = "bash"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd recovery && python3 -m unittest tests.test_cli_contract.BashContractTests tests.test_plan_parity.BashPlanParityTests -v`
Expected: FAIL. bash reports `recoverCard.sh: No such file or directory` and exits 127.

- [ ] **Step 3: Write `recoverCard.sh`**

`recovery/recoverCard.sh`:

```bash
#!/usr/bin/env bash
# Migrate GnuPG key backups to new YubiKeys (bash implementation).
# Same options, steps, --dry-run plan and exit codes as `python3 -m ykrecover --cli`.
# See recovery/README.md.

set -o errtrace -o nounset -o pipefail -o errexit
set +x
export LC_ALL=C

readonly EXIT_OK=0 EXIT_USAGE=1 EXIT_BACKUP=2 EXIT_CARD=3 EXIT_INTERRUPT=130
readonly DEFAULT_ADMIN_PIN=12345678 DEFAULT_USER_PIN=123456
readonly TOUCH_POLICIES="off on fixed cached cached-fixed"
readonly LOOPBACK=(gpg --batch --pinentry-mode loopback)
readonly CARD_GPG=(gpg --batch --command-fd 0 --pinentry-mode loopback)

BACKUP="" MODE=clone CARDS=1 OUT="" EXPIRE=2y ALGO="" REVOKE_OLD=0 PINS=generate SAME_PINS=0
RETRIES=5,5,5 TOUCH=on TOUCH_SIG="" TOUCH_ENC="" TOUCH_AUT="" NAME="" LOGIN="" URL=""
SOURCE="" KEY="" DRY_RUN=0 YES=0
RETRY=() HOMES_CREATED=() STEP_STDIN=() STEP_NO=0 CURRENT_STEP="" CERTIFY_PASS=""
WORK="" STAGING="" CARDHOME="" VERIFYHOME="" CHOSEN=""
KEY_FP="" KEY_ID="" HAS_SECRET=0
CAND_KIND=() CAND_PATH=() CAND_FILES=()
UIDS=() SUB_FPR=() SUB_USAGE=() SUB_CREATED=() SUB_EXPIRES=() SUB_VALIDITY=() SUB_ALGO=()
declare -A TARGET=() TOKENS=()

die() { # Prints an error and exits with the given code.
  local code=$1
  shift
  printf 'error: %s\n' "$*" >&2
  exit "$code"
}

warn() { printf 'warning: %s\n' "$*" >&2; }

info() { printf '%s\n' "$*" >&2; }

usage() {
  cat >&2 <<'EOF'
usage: recoverCard.sh --backup DIR [--mode clone|renew|rotate] [--cards N] [--out DIR]
         [--expire SPEC] [--algo rsa4096|ed25519] [--revoke-old] [--pins generate|enter]
         [--same-pins] [--retries U,R,A] [--touch POLICY] [--touch-sig|--touch-enc|--touch-aut POLICY]
         [--name NAME] [--login LOGIN] [--url URL] [--source export|subkeys-only|gnupghome]
         [--key FPR] [--dry-run] [--yes]
       recoverCard.sh update-stubs [--import FILE] [--remove-serial SERIAL ...] [--cards N]
EOF
}

bad_usage() { usage; exit "$EXIT_USAGE"; }

one_of() { # one_of VALUE "a b c"
  local v
  for v in $2; do [[ $1 == "$v" ]] && return 0; done
  return 1
}

# --- prompts (stderr), answers from stdin; EOF aborts with 130

read_line() { # read_line PROMPT VAR
  local __value
  printf '%s ' "$1" >&2
  if ! IFS= read -r __value; then printf '\n' >&2; die "$EXIT_INTERRUPT" "aborted"; fi
  printf -v "$2" '%s' "$__value"
}

ask_secret() { # ask_secret PROMPT VAR; no echo on a terminal
  local __value
  printf '%s ' "$1" >&2
  if [[ -t 0 ]]; then
    if ! IFS= read -rs __value; then printf '\n' >&2; die "$EXIT_INTERRUPT" "aborted"; fi
    printf '\n' >&2
  elif ! IFS= read -r __value; then
    printf '\n' >&2
    die "$EXIT_INTERRUPT" "aborted"
  fi
  printf -v "$2" '%s' "$__value"
}

confirm() { # confirm PROMPT → status
  local answer
  read_line "$1 [y/N]" answer
  [[ ${answer,,} == y || ${answer,,} == yes ]]
}

# --- options

parse_args() {
  while (( $# )); do
    case $1 in
      --backup | --mode | --cards | --out | --expire | --algo | --pins | --retries | --touch | \
        --touch-sig | --touch-enc | --touch-aut | --name | --login | --url | --source | --key)
        (( $# >= 2 )) || bad_usage
        case $1 in
          --backup) BACKUP=$2 ;;
          --mode) one_of "$2" "clone renew rotate" || bad_usage; MODE=$2 ;;
          --cards) CARDS=$2 ;;
          --out) OUT=$2 ;;
          --expire) EXPIRE=$2 ;;
          --algo) one_of "$2" "rsa4096 ed25519" || bad_usage; ALGO=$2 ;;
          --pins) one_of "$2" "generate enter" || bad_usage; PINS=$2 ;;
          --retries) RETRIES=$2 ;;
          --touch) one_of "$2" "$TOUCH_POLICIES" || bad_usage; TOUCH=$2 ;;
          --touch-sig) one_of "$2" "$TOUCH_POLICIES" || bad_usage; TOUCH_SIG=$2 ;;
          --touch-enc) one_of "$2" "$TOUCH_POLICIES" || bad_usage; TOUCH_ENC=$2 ;;
          --touch-aut) one_of "$2" "$TOUCH_POLICIES" || bad_usage; TOUCH_AUT=$2 ;;
          --name) NAME=$2 ;;
          --login) LOGIN=$2 ;;
          --url) URL=$2 ;;
          --source) one_of "$2" "export subkeys-only gnupghome" || bad_usage; SOURCE=$2 ;;
          --key) KEY=$2 ;;
        esac
        shift ;;
      --revoke-old) REVOKE_OLD=1 ;;
      --same-pins) SAME_PINS=1 ;;
      --dry-run) DRY_RUN=1 ;;
      --yes) YES=1 ;;
      -h | --help) usage; exit "$EXIT_OK" ;;
      *) bad_usage ;;
    esac
    shift
  done
  [[ -n $BACKUP ]] || bad_usage
}

validate_options() {
  local out src r
  [[ $CARDS =~ ^[0-9]+$ ]] && (( CARDS >= 1 )) || die "$EXIT_USAGE" "--cards must be 1 or more"
  [[ $RETRIES =~ ^[0-9]{1,2},[0-9]{1,2},[0-9]{1,2}$ ]] || die "$EXIT_USAGE" "--retries expects U,R,A such as 5,5,5"
  IFS=, read -r -a RETRY <<<"$RETRIES"
  for r in "${RETRY[@]}"; do
    (( 10#$r >= 1 )) || die "$EXIT_USAGE" "retries must be three numbers from 1 to 99"
  done
  if [[ -n $ALGO || $REVOKE_OLD == 1 ]] && [[ $MODE != rotate ]]; then
    die "$EXIT_USAGE" "--algo and --revoke-old need --mode rotate"
  fi
  (( DRY_RUN )) && return 0
  [[ -n $OUT ]] || die "$EXIT_USAGE" "--out is required"
  out=$(realpath -m -- "$OUT")
  src=$(realpath -m -- "$BACKUP")
  if [[ $out == "$src" || $out == "$src"/* ]]; then die "$EXIT_USAGE" "--out must be outside the backup folder"; fi
  if [[ -d $out ]] && compgen -G "$out/secret-*.key" >/dev/null; then
    die "$EXIT_USAGE" "$out already holds key files; choose an empty folder"
  fi
}

runtime_base() {
  local base=${XDG_RUNTIME_DIR:-}
  if [[ -n $base && -d $base && ${#base} -le 60 ]]; then printf '%s' "$base"; else printf '/tmp'; fi
}

preflight() {
  local tools=(gpg gpgconf) t missing=() ver major minor iface
  (( DRY_RUN )) || tools+=(gpg-connect-agent ykman)
  for t in "${tools[@]}"; do command -v "$t" >/dev/null || missing+=("$t"); done
  (( ${#missing[@]} == 0 )) || die "$EXIT_USAGE" "missing tools: ${missing[*]}"
  ver=$(gpg --version | awk 'NR == 1 {print $NF}')
  IFS=. read -r major minor _ <<<"$ver"
  if ! (( major > 2 || (major == 2 && minor >= 2) )); then die "$EXIT_USAGE" "gpg 2.2 or newer is required"; fi
  (( DRY_RUN )) && return 0
  for iface in /sys/class/net/*; do
    if [[ ${iface##*/} != lo && $(cat "$iface/operstate" 2>/dev/null) == up ]]; then
      warn "network interface ${iface##*/} is up; an air-gapped system is recommended"
    fi
  done
  if [[ $(stat -f -c %T "$(runtime_base)") != tmpfs ]]; then
    warn "$(runtime_base) is not tmpfs; key material may touch disk"
  fi
}

# --- throwaway GnuPG homes

new_home() { # new_home LABEL VAR
  local dir
  dir=$(mktemp -d "$(runtime_base)/ykr-$1-XXXXXXXX")
  chmod 700 "$dir"
  HOMES_CREATED+=("$dir")
  printf -v "$2" '%s' "$dir"
}

kill_agents() { GNUPGHOME=$1 gpgconf --kill all >/dev/null 2>&1 || true; }

destroy_home() { kill_agents "$1"; rm -rf -- "$1"; }

clone_home() { # clone_home SRC LABEL VAR
  local dst
  kill_agents "$1"
  new_home "$2" "$3"
  dst=${!3}
  cp -a -- "$1/." "$dst/"
  rm -f -- "$dst"/S.* "$dst"/*.lock "$dst"/.#* "$dst"/random_seed
}

cleanup() {
  local dir
  for dir in "${HOMES_CREATED[@]}"; do
    if [[ -d $dir ]]; then destroy_home "$dir"; fi
  done
  CERTIFY_PASS=""
}

trap cleanup EXIT
trap 'exit "$EXIT_INTERRUPT"' INT TERM

# --- backup detection and loading (sources are only read)

add_candidate() { # add_candidate KIND DIR FILE...
  CAND_KIND+=("$1")
  CAND_PATH+=("$2")
  shift 2
  local IFS=$'\n'
  CAND_FILES+=("$*")
}

detect_dir() {
  local dir=$1 f files=() certify=() subkeys=()
  if [[ -d $dir/private-keys-v1.d ]] && { [[ -f $dir/pubring.kbx ]] || [[ -f $dir/pubring.gpg ]]; }; then
    for f in private-keys-v1.d pubring.kbx pubring.gpg trustdb.gpg; do
      if [[ -e $dir/$f ]]; then files+=("$dir/$f"); fi
    done
    add_candidate gnupghome "$dir" "${files[@]}"
  fi
  shopt -s nullglob
  certify=("$dir"/secret-*-Certify.key)
  subkeys=("$dir"/secret-*-Subkeys.key)
  shopt -u nullglob
  if (( ${#certify[@]} )); then
    add_candidate export "$dir" "${certify[@]}"
  elif (( ${#subkeys[@]} )); then
    add_candidate subkeys-only "$dir" "${subkeys[@]}"
  fi
}

detect() {
  local root=${BACKUP%/} sub
  [[ -d $root ]] || die "$EXIT_BACKUP" "backup folder not found: $BACKUP"
  detect_dir "$root"
  for sub in "$root"/*/; do
    sub=${sub%/}
    if [[ -d $sub && ${sub##*/} != private-keys-v1.d ]]; then detect_dir "$sub"; fi
  done
}

choose_candidate() {
  local i matches=() listing=""
  for i in "${!CAND_KIND[@]}"; do
    if [[ -z $SOURCE || ${CAND_KIND[i]} == "$SOURCE" ]]; then matches+=("$i"); fi
  done
  if (( ${#matches[@]} == 1 )); then CHOSEN=${matches[0]}; return 0; fi
  (( ${#matches[@]} )) ||
    die "$EXIT_BACKUP" "no backup found (expected secret-*-Certify.key, secret-*-Subkeys.key or a GnuPG home)"
  for i in "${matches[@]}"; do listing+=$'\n'"  ${CAND_KIND[i]}: ${CAND_PATH[i]}"; done
  die "$EXIT_BACKUP" "several backups found; narrow --backup or pass --source:$listing"
}

list_keys() { # list_keys HOME → "sec FPR KEYID SECRET" | "uid TEXT" | "ssb FPR USAGE CREATED EXPIRES VALIDITY ALGO"
  GNUPGHOME=$1 gpg --batch --with-colons --with-keygrip --fixed-list-mode --list-secret-keys 2>/dev/null |
    awk -F: '
      $1 == "sec" { insec = 1; insub = 0; kid = $5; secret = ($15 == "#") ? 0 : 1; next }
      $1 == "ssb" { insub = 1; insec = 0; usage = $12; created = $6 + 0; expires = $7 + 0
                    validity = $2; algo = ($17 != "") ? $17 : "rsa" $3; next }
      $1 == "fpr" && insec { print "sec", $10, kid, secret; insec = 0; next }
      $1 == "fpr" && insub { print "ssb", $10, usage, created, expires, validity, algo; insub = 0; next }
      $1 == "uid" { print "uid", $10 }'
}

load_bundle() { # load_bundle HOME: chooses the key (--key) and fills KEY_* and SUB_* arrays
  local kind a b c d e f line current="" want=${KEY^^} fps=() matches=() listing=""
  want=${want// /}
  while read -r kind a _; do
    if [[ $kind == sec ]]; then fps+=("$a"); fi
  done < <(list_keys "$1")
  (( ${#fps[@]} )) || die "$EXIT_BACKUP" "no secret keys in ${CAND_PATH[CHOSEN]}"
  for a in "${fps[@]}"; do
    if [[ -z $want || $a == *"$want" ]]; then matches+=("$a"); fi
  done
  if [[ -n $want ]] && (( ${#matches[@]} == 0 )); then die "$EXIT_BACKUP" "no key matches --key $KEY"; fi
  if (( ${#matches[@]} != 1 )); then
    for a in "${matches[@]}"; do listing+=$'\n'"  $a"; done
    die "$EXIT_BACKUP" "several keys found; choose one with --key:$listing"
  fi
  KEY_FP=${matches[0]}
  UIDS=() SUB_FPR=() SUB_USAGE=() SUB_CREATED=() SUB_EXPIRES=() SUB_VALIDITY=() SUB_ALGO=()
  while IFS= read -r line; do
    read -r kind a b c d e f <<<"$line"
    case $kind in
      sec) current=$a; if [[ $a == "$KEY_FP" ]]; then KEY_ID=$b; HAS_SECRET=$c; fi ;;
      uid) if [[ $current == "$KEY_FP" ]]; then UIDS+=("${line#uid }"); fi ;;
      ssb)
        if [[ $current == "$KEY_FP" ]]; then
          SUB_FPR+=("$a"); SUB_USAGE+=("$b"); SUB_CREATED+=("$c"); SUB_EXPIRES+=("$d")
          SUB_VALIDITY+=("$e"); SUB_ALGO+=("$f")
        fi ;;
    esac
  done < <(list_keys "$1")
}

load_candidate() {
  local i=$1 f
  if [[ ${CAND_KIND[i]} == gnupghome ]]; then
    kill_agents "$STAGING"
    while IFS= read -r f; do
      cp -R -- "$f" "$STAGING/"
    done <<<"${CAND_FILES[i]}"
    chmod -R u+rwX,go-rwx "$STAGING"
  else
    while IFS= read -r f; do
      GNUPGHOME=$STAGING gpg --batch --import "$f" 2>"$WORK/import.err" ||
        die "$EXIT_BACKUP" "cannot import $f: $(cat "$WORK/import.err")"
    done <<<"${CAND_FILES[i]}"
  fi
  load_bundle "$STAGING"
}

usage_word() { case $1 in s) printf sign ;; e) printf encrypt ;; a) printf auth ;; esac; }

select_targets() { # the newest non-revoked subkey for each usage
  local u i best created
  for u in s e a; do
    best="" created=-1
    for i in "${!SUB_FPR[@]}"; do
      [[ ${SUB_USAGE[i]} == *"$u"* && ${SUB_VALIDITY[i]} != r ]] || continue
      if (( SUB_CREATED[i] > created )); then created=${SUB_CREATED[i]}; best=${SUB_FPR[i]}; fi
    done
    [[ -n $best ]] || die "$EXIT_BACKUP" "key $KEY_FP has no $(usage_word "$u") subkey"
    TARGET[$u]=$best
  done
}

unique_targets() { # unique_targets ARRAY_NAME: target fingerprints in s, e, a order without repeats
  local -n _list=$1
  local u
  _list=()
  for u in s e a; do
    if [[ " ${_list[*]} " != *" ${TARGET[$u]} "* ]]; then _list+=("${TARGET[$u]}"); fi
  done
}

check_bundle() {
  local now f i fprs=() stale=()
  if [[ $MODE != clone ]] && (( ! HAS_SECRET )); then
    die "$EXIT_BACKUP" "--mode $MODE needs the Certify key, but this backup only has subkeys"
  fi
  now=$(date +%s)
  unique_targets fprs
  for f in "${fprs[@]}"; do
    for i in "${!SUB_FPR[@]}"; do
      if [[ ${SUB_FPR[i]} == "$f" ]] && (( SUB_EXPIRES[i] > 0 && SUB_EXPIRES[i] <= now )); then stale+=("$f"); fi
    done
  done
  if (( ${#stale[@]} )) && [[ $MODE == clone ]]; then
    die "$EXIT_BACKUP" "subkeys have expired: ${stale[*]}; use --mode renew or --mode rotate"
  fi
}

# --- canonical steps (byte-identical to the Python plan)

subst() { # replaces every token in $1
  local s=$1 k
  for k in "${!TOKENS[@]}"; do s=${s//"$k"/"${TOKENS[$k]}"}; done
  printf '%s' "$s"
}

subst_public() { # like subst, but secrets stay as placeholders
  local s=$1 k
  for k in "${!TOKENS[@]}"; do
    case $k in '<CERTIFY_PASS>' | '<ADMIN_PIN>' | '<USER_PIN>') continue ;; esac
    s=${s//"$k"/"${TOKENS[$k]}"}
  done
  printf '%s' "$s"
}

step() { # step PHASE HOME CHECK ARGV...; stdin lines come from STEP_STDIN
  local phase=$1 home=$2 check=$3 line rc=0
  shift 3
  if (( DRY_RUN )); then
    STEP_NO=$((STEP_NO + 1))
    printf 'STEP %s %s %s %s\n' "$STEP_NO" "$phase" "$home" "$*"
    for line in "${STEP_STDIN[@]}"; do printf 'STDIN %s\n' "$line"; done
    if [[ -n $check ]]; then printf 'CHECK %s\n' "$check"; fi
  else
    run_step "$phase" "$home" "$check" "$@" || rc=1
  fi
  STEP_STDIN=()
  return "$rc"
}

kill_step() { step "$1" "$2" '' gpgconf --kill all; }

algo_for() { # algo_for ALGO USAGE
  if [[ $1 == rsa4096 ]]; then printf rsa4096; elif [[ $2 == e ]]; then printf cv25519; else printf ed25519; fi
}

rotate_algo() {
  local i
  if [[ -n $ALGO ]]; then printf '%s' "$ALGO"; return; fi
  for i in "${!SUB_FPR[@]}"; do
    if [[ ${SUB_FPR[i]} == "${TARGET[s]}" ]]; then
      if [[ ${SUB_ALGO[i]} == rsa* ]]; then printf rsa4096; else printf ed25519; fi
      return
    fi
  done
}

mode_steps() {
  local u old algo fprs=()
  case $MODE in
    renew)
      unique_targets fprs
      STEP_STDIN=('<CERTIFY_PASS>')
      step mode '<STAGING>' '' "${LOOPBACK[@]}" --passphrase-fd 0 --quick-set-expire "$KEY_FP" "$EXPIRE" \
        "${fprs[@]}" || return 1 ;;
    rotate)
      algo=$(rotate_algo)
      for u in s e a; do
        STEP_STDIN=('<CERTIFY_PASS>')
        step mode '<STAGING>' '' "${LOOPBACK[@]}" --passphrase-fd 0 --quick-add-key "$KEY_FP" \
          "$(algo_for "$algo" "$u")" "$(usage_word "$u")" "$EXPIRE" || return 1
      done
      if (( REVOKE_OLD )); then
        unique_targets fprs
        for old in "${fprs[@]}"; do
          kill_step mode '<STAGING>' || return 1
          STEP_STDIN=("key $old" revkey y 0 '' y '<CERTIFY_PASS>' save)
          step mode '<STAGING>' '' "${LOOPBACK[@]}" --command-fd 0 --edit-key "$KEY_FP" || return 1
        done
      fi ;;
  esac
}

backup_steps() {
  [[ $MODE != clone ]] || return 0
  STEP_STDIN=('<CERTIFY_PASS>')
  step backup '<STAGING>' '' "${LOOPBACK[@]}" --passphrase-fd 0 --yes --armor --output \
    "<OUT>/secret-$KEY_ID-Certify.key" --export-secret-keys "$KEY_FP" || return 1
  STEP_STDIN=('<CERTIFY_PASS>')
  step backup '<STAGING>' '' "${LOOPBACK[@]}" --passphrase-fd 0 --yes --armor --output \
    "<OUT>/secret-$KEY_ID-Subkeys.key" --export-secret-subkeys "$KEY_FP" || return 1
  step backup '<STAGING>' '' gpg --batch --yes --armor --output "<OUT>/public-$KEY_ID-<DATE>.asc" \
    --export "$KEY_FP" || return 1
}

touch_for() {
  case $1 in sig) printf '%s' "${TOUCH_SIG:-$TOUCH}" ;; dec) printf '%s' "${TOUCH_ENC:-$TOUCH}" ;;
    aut) printf '%s' "${TOUCH_AUT:-$TOUCH}" ;; esac
}

card_steps() { # card_steps INDEX
  local phase="card$1" serial="<SERIAL$1>" h='<CARDHOME>' slot usage ref
  local -a holder=(admin login '<LOGIN>' '<ADMIN_PIN>')
  if [[ -n $NAME ]]; then holder+=(name '<SURNAME>' '<GIVEN>'); fi
  if [[ -n $URL ]]; then holder+=(url '<URL>'); fi
  kill_step "$phase" "$h" || return 1
  step "$phase" "$h" '' ykman --device "$serial" openpgp reset -f || return 1
  # Retries first: changing the retry counters may reset the PINs to defaults.
  kill_step "$phase" "$h" || return 1
  step "$phase" "$h" '' ykman --device "$serial" openpgp access set-retries "${RETRY[@]}" -f -a '<DEFAULT_ADMIN_PIN>' ||
    return 1
  kill_step "$phase" "$h" || return 1
  STEP_STDIN=(3 '<DEFAULT_ADMIN_PIN>' '<ADMIN_PIN>' '<ADMIN_PIN>' q)
  step "$phase" "$h" '' "${CARD_GPG[@]}" --change-pin || return 1
  kill_step "$phase" "$h" || return 1
  STEP_STDIN=(1 '<DEFAULT_USER_PIN>' '<USER_PIN>' '<USER_PIN>' q)
  step "$phase" "$h" '' "${CARD_GPG[@]}" --change-pin || return 1
  kill_step "$phase" "$h" || return 1
  STEP_STDIN=("${holder[@]}" quit)
  step "$phase" "$h" '' "${CARD_GPG[@]}" --edit-card || return 1
  for slot in 1 2 3; do
    case $slot in 1) usage=s ;; 2) usage=e ;; 3) usage=a ;; esac
    kill_step "$phase" "$h" || return 1
    STEP_STDIN=("key ${TARGET[$usage]}" keytocard "$slot" '<CERTIFY_PASS>' '<ADMIN_PIN>' save)
    step "$phase" "$h" '' "${CARD_GPG[@]}" --edit-key "$KEY_FP" || return 1
  done
  for ref in sig dec aut; do
    kill_step "$phase" "$h" || return 1
    step "$phase" "$h" '' ykman --device "$serial" openpgp keys set-touch "$ref" "$(touch_for "$ref")" -f -a \
      '<ADMIN_PIN>' || return 1
  done
}

verify_steps() { # verify_steps INDEX
  local phase="verify$1" v='<VERIFYHOME>'
  local -a pin=(gpg --batch --pinentry-mode loopback --passphrase-fd 0 --yes)
  step "$phase" '<STAGING>' '' gpg --batch --yes --armor --output "$v/pub.asc" --export "$KEY_FP" || return 1
  step "$phase" "$v" '' gpg --batch --import "$v/pub.asc" || return 1
  step "$phase" "$v" cardstatus gpg --batch --with-colons --card-status || return 1
  STEP_STDIN=('<USER_PIN>')
  step "$phase" "$v" '' "${pin[@]}" --local-user "${TARGET[s]}!" --output "$v/test.sig" --detach-sign \
    "$v/test.txt" || return 1
  step "$phase" "$v" '' gpg --batch --verify "$v/test.sig" "$v/test.txt" || return 1
  step "$phase" "$v" '' gpg --batch --yes --trust-model always --recipient "${TARGET[e]}!" --output \
    "$v/test.gpg" --encrypt "$v/test.txt" || return 1
  kill_step "$phase" "$v" || return 1
  STEP_STDIN=('<USER_PIN>')
  step "$phase" "$v" roundtrip "${pin[@]}" --output "$v/test.out" --decrypt "$v/test.gpg" || return 1
}

finish_steps() {
  step finish '<STAGING>' '' gpg --batch --yes --armor --output "<OUT>/public-$KEY_ID-<DATE>.asc" \
    --export "$KEY_FP" || return 1
  step finish '<STAGING>' '' gpg --batch --yes --output "<OUT>/ssh-$KEY_ID.pub" --export-ssh-key \
    "${TARGET[a]}!" || return 1
}

dry_run() {
  local i
  mode_steps
  backup_steps
  if [[ $MODE == rotate ]]; then TARGET=([s]='<NEW_FPR_S>' [e]='<NEW_FPR_E>' [a]='<NEW_FPR_A>'); fi
  for ((i = 1; i <= CARDS; i++)); do
    card_steps "$i"
    verify_steps "$i"
  done
  finish_steps
}

main() {
  if [[ ${1:-} == update-stubs ]]; then
    shift
    update_stubs "$@"
  fi
  parse_args "$@"
  validate_options
  preflight
  new_home work WORK
  new_home staging STAGING
  detect
  choose_candidate
  load_candidate "$CHOSEN"
  select_targets
  check_bundle
  if (( DRY_RUN )); then
    dry_run
    exit "$EXIT_OK"
  fi
  run_live
}

main "$@"
```

- [ ] **Step 4: Run the bash tests to verify they pass**

Run: `cd recovery && python3 -m unittest tests.test_cli_contract.BashContractTests tests.test_plan_parity.BashPlanParityTests -v`
Expected: `OK` (13 tests). If a parity subtest fails, `diff <(bash recoverCard.sh <args> --dry-run) tests/golden/<case>.plan` shows the first differing line. Fix bash and never the golden file, unless the Python output itself is wrong (then fix Python and regenerate with `UPDATE_GOLDEN=1`).

- [ ] **Step 5: shellcheck**

Run: `shellcheck --severity=error recovery/recoverCard.sh && shellcheck recovery/recoverCard.sh`
Expected: the first prints nothing. The second may report SC2034 (variables used only in later tasks) and SC2317 for not-yet-defined callees. Fix anything else.

- [ ] **Step 6: Commit**

```bash
git add recovery/recoverCard.sh recovery/tests/test_cli_contract.py recovery/tests/test_plan_parity.py
git commit -m "recovery: bash implementation of options, backup loading and dry-run"
```

---

### Task 10: Bash implementation — live run

**Files:**

- Modify: `recovery/recoverCard.sh` (insert functions immediately above the final line `main "$@"`)
- Modify: `recovery/tests/test_fake_hardware.py` (add bash class)

**Interfaces:**

- Consumes: Task 9 functions (`step`, builders, `load_bundle`, `select_targets`, homes), the fake hardware contract from Task 6.
- Produces: `run_step`, `run_check`, `describe_key`, `check_passphrase`, `unlock`, `generate_pins`, `random_login`, `ask_pin`, `assign_pins`, `split_name`, `wait_for_card`, `confirm_reset`, `read_choice`, `write_card`, `write_cards`, `finish`, `run_live`.

- [ ] **Step 1: Add the bash test class (failing)**

Append to `recovery/tests/test_fake_hardware.py` (above the `if __name__` block):

```python
class BashFakeHardwareTests(FakeHardwareTests, unittest.TestCase):
    IMPL = "bash"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd recovery && python3 -m unittest tests.test_fake_hardware.BashFakeHardwareTests -v`
Expected: most tests FAIL with exit 127 and stderr `run_live: command not found`.

- [ ] **Step 3: Insert the live-run functions above `main "$@"`**

```bash
# --- live run

run_step() { # executes one canonical step; on failure CURRENT_STEP and $WORK/step.err describe it
  local phase=$1 home=$2 check=$3 dir arg line rc=0
  shift 3
  local -a args=()
  for arg in "$@"; do args+=("$(subst "$arg")"); done
  dir=$(subst "$home")
  CURRENT_STEP=$(subst_public "$*")
  if [[ $phase == card* || $phase == verify* ]]; then info "[$phase] $CURRENT_STEP"; fi
  if (( ${#STEP_STDIN[@]} )); then
    GNUPGHOME=$dir "${args[@]}" >"$WORK/step.out" 2>"$WORK/step.err" < <(
      for line in "${STEP_STDIN[@]}"; do printf '%s\n' "$(subst "$line")"; done
    ) || rc=$?
  else
    GNUPGHOME=$dir "${args[@]}" </dev/null >"$WORK/step.out" 2>"$WORK/step.err" || rc=$?
  fi
  (( rc == 0 )) || return 1
  run_check "$check"
}

run_check() {
  local got want
  case $1 in
    cardstatus)
      want="${TARGET[s]^^}:${TARGET[e]^^}:${TARGET[a]^^}"
      got=$(awk -F: '$1 == "fpr" {print toupper($2) ":" toupper($3) ":" toupper($4); exit}' "$WORK/step.out")
      if [[ $got != "$want" ]]; then
        CURRENT_STEP="card fingerprints"
        printf 'card has %s, expected %s\n' "${got:-nothing}" "$want" >"$WORK/step.err"
        return 1
      fi ;;
    roundtrip)
      if ! cmp -s "$VERIFYHOME/test.out" "$VERIFYHOME/test.txt"; then
        CURRENT_STEP="decrypt round-trip"
        printf 'decrypted text differs from the original\n' >"$WORK/step.err"
        return 1
      fi ;;
  esac
}

describe_key() {
  local i mark expires targets=" ${TARGET[s]} ${TARGET[e]} ${TARGET[a]} "
  info "Key $KEY_FP"
  for i in "${!UIDS[@]}"; do info "  uid ${UIDS[i]}"; done
  for i in "${!SUB_FPR[@]}"; do
    mark=""
    if [[ $targets == *" ${SUB_FPR[i]} "* ]]; then mark=" <- card"; fi
    expires=never
    if (( SUB_EXPIRES[i] > 0 )); then expires=$(date -d "@${SUB_EXPIRES[i]}" +%F); fi
    info "  [${SUB_USAGE[i]^^}] ${SUB_ALGO[i]} ${SUB_FPR[i]} expires $expires$mark"
  done
}

check_passphrase() { # check_passphrase PASS → status
  local what=--export-secret-keys rc=0
  (( HAS_SECRET )) || what=--export-secret-subkeys
  GNUPGHOME=$STAGING gpg --batch --pinentry-mode loopback --passphrase-fd 0 --armor --yes --output /dev/null \
    "$what" "$KEY_FP" >/dev/null 2>&1 < <(printf '%s\n' "$1") || rc=$?
  kill_agents "$STAGING"
  return "$rc"
}

unlock() {
  local try
  for try in 1 2 3; do
    ask_secret "Certify key passphrase:" CERTIFY_PASS
    if check_passphrase "$CERTIFY_PASS"; then return 0; fi
    warn "wrong passphrase"
  done
  die "$EXIT_BACKUP" "wrong passphrase three times"
}

generate_pins() { # sets PAIR_ADMIN and PAIR_USER (the guide's method)
  local digits
  digits=$(LC_ALL=C tr -dc '0-9' </dev/urandom | head -c 14 || true)
  PAIR_ADMIN=${digits:0:8}
  PAIR_USER=${digits:8:6}
}

random_login() { printf 'yk.%s' "$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 16 || true)"; }

ask_pin() { # ask_pin LABEL MIN VAR
  local first second
  while true; do
    ask_secret "$1:" first
    if (( ${#first} < $2 || ${#first} > 127 )); then warn "must be $2 to 127 characters"; continue; fi
    ask_secret "Repeat $1:" second
    if [[ $first != "$second" ]]; then warn "entries differ"; continue; fi
    printf -v "$3" '%s' "$first"
    return 0
  done
}

declare -A ADMIN_PINS=() USER_PINS=() LOGINS=() SERIALS=() STATUS=() DETAIL=()
PAIR_ADMIN="" PAIR_USER="" SERIAL="" SURNAME="" GIVEN=""

assign_pins() {
  local i label have=0
  for ((i = 1; i <= CARDS; i++)); do
    if ! (( SAME_PINS && have )); then
      if [[ $PINS == generate ]]; then
        generate_pins
      else
        label="card $i"
        if (( SAME_PINS )); then label="all cards"; fi
        ask_pin "Admin PIN ($label)" 8 PAIR_ADMIN
        ask_pin "User PIN ($label)" 6 PAIR_USER
      fi
      have=1
    fi
    ADMIN_PINS[$i]=$PAIR_ADMIN
    USER_PINS[$i]=$PAIR_USER
    LOGINS[$i]=${LOGIN:-$(random_login)}
  done
}

split_name() { # sets SURNAME and GIVEN
  local -a parts
  read -r -a parts <<<"$1"
  SURNAME="" GIVEN=""
  if (( ${#parts[@]} )); then
    SURNAME=${parts[-1]}
    GIVEN="${parts[*]:0:${#parts[@]}-1}"
  fi
}

wait_for_card() { # wait_for_card INDEX DONE_LIST → SERIAL
  local answer serials=()
  while true; do
    read_line "Insert card $1 of $CARDS and press Enter" answer
    mapfile -t serials < <(ykman list --serials 2>/dev/null | grep -E '^[0-9]+$' || true)
    if (( ${#serials[@]} != 1 )); then
      warn "expected exactly one YubiKey, found ${#serials[@]}"
    elif [[ $2 == *" ${serials[0]} "* ]]; then
      warn "card ${serials[0]} was already written in this session"
    else
      SERIAL=${serials[0]}
      return 0
    fi
  done
}

confirm_reset() {
  local answer
  read_line "Type RESET $1 to erase card $1:" answer
  [[ $answer == "RESET $1" ]]
}

read_choice() { # read_choice VAR → retry | skip | abort
  local answer
  while true; do
    read_line "[r]etry, [s]kip or [a]bort?" answer
    case ${answer,,} in
      r*) printf -v "$1" retry; return 0 ;;
      s*) printf -v "$1" skip; return 0 ;;
      a*) printf -v "$1" abort; return 0 ;;
    esac
  done
}

write_card() { # write_card INDEX → status; runs with errexit suspended, so every step checks itself
  local i=$1 rc=0
  clone_home "$STAGING" card CARDHOME || return 1
  new_home verify VERIFYHOME || return 1
  printf '%s\n' "$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')" >"$VERIFYHOME/test.txt"
  split_name "$NAME"
  TOKENS["<SERIAL$i>"]=${SERIALS[$i]}
  TOKENS['<ADMIN_PIN>']=${ADMIN_PINS[$i]}
  TOKENS['<USER_PIN>']=${USER_PINS[$i]}
  TOKENS['<LOGIN>']=${LOGINS[$i]}
  TOKENS['<SURNAME>']=$SURNAME
  TOKENS['<GIVEN>']=$GIVEN
  TOKENS['<URL>']=$URL
  TOKENS['<CARDHOME>']=$CARDHOME
  TOKENS['<VERIFYHOME>']=$VERIFYHOME
  { card_steps "$i" && verify_steps "$i"; } || rc=1
  destroy_home "$CARDHOME"
  destroy_home "$VERIFYHOME"
  return "$rc"
}

write_cards() {
  local i choice done_list=" "
  gpgconf --kill scdaemon >/dev/null 2>&1 || true # release the card from the user's own scdaemon
  for ((i = 1; i <= CARDS; i++)); do
    wait_for_card "$i" "$done_list"
    SERIALS[$i]=$SERIAL
    done_list+="$SERIAL "
    if ! confirm_reset "$SERIAL"; then
      STATUS[$i]=SKIPPED
      DETAIL[$i]="reset not confirmed"
      continue
    fi
    while true; do
      if write_card "$i"; then
        STATUS[$i]=OK
        DETAIL[$i]=""
        info "[card $i] $SERIAL: OK"
        break
      fi
      warn "card $SERIAL failed at: $CURRENT_STEP"
      tail -c 2000 "$WORK/step.err" >&2 || true
      read_choice choice
      if [[ $choice == retry ]]; then continue; fi
      STATUS[$i]=FAILED
      DETAIL[$i]=$CURRENT_STEP
      if [[ $choice == abort ]]; then die "$EXIT_INTERRUPT" "aborted"; fi
      break
    done
  done
}

finish() {
  local i all_ok=1 public
  finish_steps || warn "export failed: $CURRENT_STEP"
  for ((i = 1; i <= CARDS; i++)); do
    if [[ ${STATUS[$i]:-} == OK ]]; then
      printf 'Card %s: Admin PIN %s  User PIN %s\n' "${SERIALS[$i]}" "${ADMIN_PINS[$i]}" "${USER_PINS[$i]}" >&2
    fi
  done
  info "Summary:"
  for ((i = 1; i <= CARDS; i++)); do
    info "card $i ${SERIALS[$i]:--} ${STATUS[$i]:-pending} ${DETAIL[$i]:-}"
    if [[ ${STATUS[$i]:-} != OK ]]; then all_ok=0; fi
  done
  public="$OUT/public-$KEY_ID-$(date +%F).asc"
  info "On your daily machine run: recoverCard.sh update-stubs --import $public --cards $CARDS"
  if (( all_ok )); then return "$EXIT_OK"; fi
  return "$EXIT_CARD"
}

run_live() {
  local rc=0
  describe_key
  unlock
  if ! (( YES )) && ! confirm "Write subkeys of $KEY_FP to $CARDS card(s)?"; then exit "$EXIT_INTERRUPT"; fi
  mkdir -p -m 700 -- "$OUT"
  TOKENS=(['<CERTIFY_PASS>']=$CERTIFY_PASS ['<OUT>']=$OUT ['<DATE>']=$(date +%F)
    ['<DEFAULT_ADMIN_PIN>']=$DEFAULT_ADMIN_PIN ['<DEFAULT_USER_PIN>']=$DEFAULT_USER_PIN ['<STAGING>']=$STAGING)
  if ! { mode_steps && backup_steps; }; then
    die "$EXIT_BACKUP" "$MODE failed: $CURRENT_STEP: $(tail -c 2000 "$WORK/step.err")"
  fi
  if [[ $MODE != clone ]]; then
    load_bundle "$STAGING"
    select_targets
    kill_agents "$STAGING"
    until confirm "New backup written to $OUT. Saved it to offline storage?"; do
      warn "copy the new backup before writing cards"
    done
  fi
  assign_pins
  write_cards
  finish || rc=$?
  exit "$rc"
}
```

- [ ] **Step 4: Run the bash fake-hardware tests**

Run: `cd recovery && python3 -m unittest tests.test_fake_hardware.BashFakeHardwareTests -v`
Expected: `OK` (12 tests).

- [ ] **Step 5: Run the whole suite and shellcheck**

Run: `cd recovery && python3 -m unittest discover -s tests -t . -v && shellcheck --severity=error recoverCard.sh`
Expected: `OK`; shellcheck prints nothing.

- [ ] **Step 6: Commit**

```bash
git add recovery/recoverCard.sh recovery/tests/test_fake_hardware.py
git commit -m "recovery: bash live run with card writing and verification"
```

---

### Task 11: Bash implementation — update-stubs

**Files:**

- Modify: `recovery/recoverCard.sh` (insert above `main "$@"`)
- Modify: `recovery/tests/test_stubs.py` (add bash class)

**Interfaces:**

- Consumes: the update-stubs contract (Task 7), `read_line`, `confirm`, `die`.
- Produces: `update_stubs ARGS...` (always exits).

- [ ] **Step 1: Add the bash test class (failing)**

Append to `recovery/tests/test_stubs.py` (above the `if __name__` block):

```python
class BashStubsTests(StubsTests, unittest.TestCase):
    IMPL = "bash"
```

Run: `cd recovery && python3 -m unittest tests.test_stubs.BashStubsTests -v`
Expected: FAIL with exit 127 and `update_stubs: command not found`.

- [ ] **Step 2: Insert `update_stubs` above `main "$@"`**

```bash
# --- update-stubs: the only part that touches the user's own GNUPGHOME

update_stubs() {
  local import="" cards=1 home=${GNUPGHOME:-$HOME/.gnupg} serial f dest i answer t
  local -a serials=() stubs=()
  while (( $# )); do
    case $1 in
      --import) (( $# >= 2 )) || bad_usage; import=$2; shift ;;
      --remove-serial) (( $# >= 2 )) || bad_usage; serials+=("$2"); shift ;;
      --cards) (( $# >= 2 )) || bad_usage; cards=$2; shift ;;
      -h | --help) usage; exit "$EXIT_OK" ;;
      *) bad_usage ;;
    esac
    shift
  done
  [[ $cards =~ ^[0-9]+$ ]] && (( cards >= 1 )) || die "$EXIT_USAGE" "--cards must be 1 or more"
  for t in gpg gpg-connect-agent; do command -v "$t" >/dev/null || die "$EXIT_USAGE" "missing tools: $t"; done
  if [[ -n $import ]]; then
    gpg --batch --import "$import" 2>/dev/null || die "$EXIT_USAGE" "import failed: $import"
  fi
  for serial in "${serials[@]}"; do
    stubs=()
    shopt -s nullglob
    for f in "$home"/private-keys-v1.d/*.key; do
      if awk -v s="$serial" '$1 == "Token:" && index($2, s) {found = 1} END {exit !found}' "$f"; then
        stubs+=("$f")
      fi
    done
    shopt -u nullglob
    if (( ${#stubs[@]} == 0 )); then info "no stubs reference card $serial"; continue; fi
    confirm "Move ${#stubs[@]} stub(s) for card $serial out of the keyring?$(printf '\n  %s' "${stubs[@]##*/}")
" || continue
    dest=$home/stub-backup-$(date +%Y%m%d-%H%M%S)
    mkdir -p -m 700 -- "$dest"
    mv -- "${stubs[@]}" "$dest/"
    info "moved to $dest"
  done
  for ((i = 1; i <= cards; i++)); do
    read_line "Insert card $i of $cards and press Enter" answer
    gpg-connect-agent "scd serialno" "learn --force" /bye >/dev/null || warn "learn failed for card $i"
    gpg --batch --card-status >&2 || true
  done
  exit "$EXIT_OK"
}
```

- [ ] **Step 3: Run the tests to verify they pass**

Run: `cd recovery && python3 -m unittest tests.test_stubs -v && shellcheck --severity=error recoverCard.sh`
Expected: `OK` (8 tests); shellcheck prints nothing.

- [ ] **Step 4: Commit**

```bash
git add recovery/recoverCard.sh recovery/tests/test_stubs.py
git commit -m "recovery: bash update-stubs"
```

---

### Task 12: Nix LiveCD integration

**Files:**

- Modify: `nix/flake.nix` (the `let` block that defines `yubikeyGuide`, `environment.systemPackages`, `system.activationScripts.yubikeyGuide`)

**Interfaces:**

- Consumes: `recovery/ykrecover`, `recovery/recoverCard.sh` via `${self}/../recovery`, the same path style the flake already uses for `config/` and `README.md`.
- Produces: commands `yubikey-recovery` (GUI; `--cli` for terminal) and `recoverCard` on the ISO's PATH, plus a "YubiKey Recovery" desktop shortcut.

- [ ] **Step 1: Add the packages to the `let` block**

In `nix/flake.nix`, directly after the `yubikeyGuide = pkgs.symlinkJoin { … };` definition, add:

```nix
              recoveryPython = pkgs.python3.withPackages (ps: [ps.tkinter]);
              recoveryGui = pkgs.writeShellScriptBin "yubikey-recovery" ''
                export PYTHONPATH="${self}/../recovery''${PYTHONPATH:+:$PYTHONPATH}"
                exec ${recoveryPython}/bin/python3 -m ykrecover "$@"
              '';
              recoveryScript = pkgs.writeShellScriptBin "recoverCard" ''
                exec ${pkgs.bash}/bin/bash "${self}/../recovery/recoverCard.sh" "$@"
              '';
              recoveryShortcut = pkgs.makeDesktopItem {
                name = "yubikey-recovery";
                icon = "${pkgs.yubioath-flutter}/share/icons/com.yubico.yubioath.png";
                desktopName = "YubiKey Recovery";
                genericName = "Migrate GnuPG key backups to new YubiKeys";
                comment = "Write backed-up GnuPG subkeys to new YubiKeys";
                categories = ["Utility"];
                exec = "${recoveryGui}/bin/yubikey-recovery";
              };
              yubikeyRecovery = pkgs.symlinkJoin {
                name = "yubikey-recovery";
                paths = [recoveryGui recoveryScript recoveryShortcut];
              };
```

- [ ] **Step 2: Add it to the system packages**

In `environment.systemPackages`, directly after the `yubikeyGuide` entry, add:

```nix
                # Migrate key backups to new YubiKeys (`yubikey-recovery`, or
                # `recoverCard` in a terminal). See recovery/README.md.
                yubikeyRecovery
```

- [ ] **Step 3: Add the desktop shortcut**

In `system.activationScripts.yubikeyGuide`, after the line linking `yubikey-guide.desktop`, add:

```nix
                ln -sf ${yubikeyRecovery}/share/applications/yubikey-recovery.desktop ${desktopDir}
```

- [ ] **Step 4: Format and evaluate**

If Nix is available:

- Run `cd nix && nix fmt` and expect no diff beyond formatting.
- Run `nix build --experimental-features "nix-command flakes" nix#nixosConfigurations.yubikeyLive.x86_64-linux.config.system.build.isoImage` and expect a successful build.
- Boot the ISO in QEMU (command in `CLAUDE.md`) and check that `yubikey-recovery --cli --help` prints usage, `recoverCard --help` prints usage, and the desktop has a "YubiKey Recovery" icon.

If Nix is not available, record "Nix build not verified" in the commit message body and in the hand-off; do not claim it works.

- [ ] **Step 5: Commit**

```bash
git add nix/flake.nix
git commit -m "nix: ship the YubiKey recovery toolset on the LiveCD"
```

---

### Task 13: CI, documentation and hardware checklist

**Files:**

- Modify: `.github/workflows/lint.yml`
- Modify: `.markdownlint-cli2.jsonc` (add `recovery/README.md` to globs)
- Create: `recovery/README.md`
- Modify: `README.md` (one tip in "Using multiple YubiKeys")
- Modify: `CLAUDE.md` (recovery commands and the parity rule)

**Interfaces:**

- Consumes: everything above.
- Produces: green CI covering both implementations, user documentation, and the manual hardware checklist (spec Testing item 4).

- [ ] **Step 1: Extend CI**

In `.github/workflows/lint.yml`, change the shellcheck step's command to:

```yaml
        run: shellcheck --severity=error scripts/*.sh piv/*.sh recovery/recoverCard.sh recovery/tests/fixtures/make_fixtures.sh
```

and add a second job after `lint`:

```yaml
  recovery:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: install GnuPG
        run: sudo apt-get update && sudo apt-get install -y gnupg scdaemon

      - name: recovery tests (Python and bash)
        working-directory: recovery
        run: python3 -m unittest discover -s tests -t . -v
```

- [ ] **Step 2: Write `recovery/README.md`**

````markdown
# YubiKey recovery

Migrate GnuPG key backups, made with this guide before `keytocard`, onto new YubiKeys. Use it when existing YubiKeys are damaged, lost or being replaced.

Two equivalent tools:

- `yubikey-recovery`: a graphical wizard (`python3 -m ykrecover` from this folder). Add `--cli` for the same flow in a terminal.
- `recoverCard.sh`: the same options, steps and exit codes in bash.

On the LiveCD built from `nix/` both are installed, and "YubiKey Recovery" is on the desktop.

> [!WARNING]
> Every card you write is **reset**: all OpenPGP keys and PINs on it are erased. The tool asks you to type `RESET <serial>` for each card.

> [!IMPORTANT]
> Run this on an air-gapped system. The Certify key passphrase and your backup are loaded into memory-backed temporary folders that are deleted when the tool exits.

## Modes

| Mode | What goes on the cards | Needs Certify key | New backup |
| - | - | - | - |
| `clone` | the backed-up subkeys, unchanged | no | no |
| `renew` | the same subkeys with a new expiry | yes | yes |
| `rotate` | newly generated subkeys | yes | yes |

Choose `rotate` if the old cards may be lost or in someone else's hands. Add `--revoke-old` to revoke the previous subkeys.

`renew` and `rotate` write a new backup to `--out` and wait for you to confirm that you saved it before any card is touched. Store the new backup the same way as the original.

## Usage

```bash
recoverCard.sh --backup /mnt/backup --out /mnt/new-backup --cards 2
recoverCard.sh --backup /mnt/backup --out /mnt/new-backup --mode renew --expire 2y --cards 2
recoverCard.sh --backup /mnt/backup --mode rotate --revoke-old --dry-run
```

`--dry-run` prints every command the run would execute, with secrets shown as `<ADMIN_PIN>`-style placeholders, and touches nothing.

Run `recoverCard.sh --help` for every option. The defaults are:

- **PINs:** generated, 8-digit Admin and 6-digit User; pass `--pins enter` to type your own.
- **Retries:** `5,5,5`.
- **Touch:** `on` for every key.
- **Cardholder:** a random login and no name, because card metadata is readable by anyone holding the card.

Exit codes:

| Code | Meaning |
| - | - |
| 0 | every card written and verified |
| 1 | usage or preflight problem |
| 2 | backup or passphrase problem |
| 3 | a card failed or was skipped |
| 130 | aborted |

## After writing the cards

On your daily machine, point GnuPG at the new cards and move aside the stubs for the damaged ones:

```bash
recoverCard.sh update-stubs --import /mnt/new-backup/public-KEYID-DATE.asc --remove-serial OLD_SERIAL --cards 2
```

If you renewed or rotated, also publish the new public key wherever you published the old one.

## How it works

1. The backup is imported into a temporary staging GnuPG home. The backup itself is only read.
2. The mode is applied in staging.
3. For each card, staging is copied into a fresh home. The card is reset, its PINs and cardholder data are set, `keytocard` moves the subkeys, and then that copy is deleted. Staging is never consumed, so a failed card can be retried safely.
4. A third home holding only the public key verifies the card:
   - the card's fingerprints match the subkeys;
   - a test signature verifies;
   - a test message decrypts.

Before every scripted gpg or ykman command, `gpgconf --kill all` clears cached passphrases and card sessions, so prompts always arrive in the expected order.

The Admin PIN is passed to `ykman` with `-a`, as in the main guide. On a multi-user machine other users could briefly see it in the process list; on the intended single-user air-gapped system that risk does not apply.

## Development

Run the tests from this folder:

```bash
python3 -m unittest discover -s tests -t . -v
```

- The Python and bash implementations must print identical `--dry-run` plans. Golden plans live in `tests/golden/`.
- After an intentional change, update the Python code, regenerate with `UPDATE_GOLDEN=1 python3 -m unittest tests.test_plan_parity`, review the diff, then make bash match.
- Card operations are tested against the fakes in `tests/fakebin/`.

## Hardware checklist

Run this on a spare YubiKey before using real backups, and again after upgrading GnuPG, ykman or YubiKey firmware. Use the test keys in `tests/fixtures/` (passphrase `test-certify-pass`).

- [ ] `clone` from `tests/fixtures/ed25519/export` to one card, with the GUI: the card shows OK. `gpg --card-status` lists the three fixture fingerprints and `Login data` starts with `yk.`.
- [ ] `clone` from `tests/fixtures/rsa4096/export` with `recoverCard.sh --touch off`: OK, and the key attributes show `rsa4096` for all three slots.
- [ ] `renew` from `tests/fixtures/expired/export`: a new backup appears in `--out`, and the card is OK.
- [ ] `rotate --algo ed25519 --revoke-old` from `tests/fixtures/rsa4096/export`: the card's fingerprints differ from the fixture's, and the exported public key shows the old subkeys as revoked.
- [ ] Pull the card out during the `keytocard` steps: the card is FAILED with gpg's message; reinsert it, choose Retry, and it succeeds.
- [ ] `update-stubs --remove-serial <spare serial>` on a test GNUPGHOME: the stubs move to `stub-backup-*`, and `gpg --card-status` lists the card again.
- [ ] Afterwards reset the spare card: `ykman openpgp reset`.
````

- [ ] **Step 3: Add `recovery/README.md` to markdownlint**

In `.markdownlint-cli2.jsonc`, change the globs line to:

```jsonc
  "globs": ["README.md", "SECENV.md", "piv/README.md", "recovery/README.md"]
```

- [ ] **Step 4: Point to it from the main README**

In `README.md`, in the "## Using multiple YubiKeys" section, after the paragraph ending with "will not recognize another YubiKey with a different serial number.", add:

```markdown
> [!TIP]
> To load backed-up keys onto a new set of YubiKeys, for example after a YubiKey is damaged or lost, use the [recovery toolset](recovery/README.md).
```

- [ ] **Step 5: Update CLAUDE.md**

In `CLAUDE.md`, add to the end of the "## Build Commands" bash block:

```bash
# Recovery toolset tests (Python + bash, fake card hardware)
cd recovery && python3 -m unittest discover -s tests -t . -v
```

and add to "## Scripts and Nix":

```markdown
- `recovery/` holds two implementations of the same tool (`ykrecover/` Python + Tkinter, `recoverCard.sh` bash). They must print byte-identical `--dry-run` plans (`recovery/tests/golden/`); change Python first, regenerate with `UPDATE_GOLDEN=1`, then make bash match.
```

- [ ] **Step 6: Run every check**

```bash
cd recovery && python3 -m unittest discover -s tests -t . -v && cd ..
shellcheck --severity=error scripts/*.sh piv/*.sh recovery/recoverCard.sh recovery/tests/fixtures/make_fixtures.sh
npx --yes markdownlint-cli2@0.23.3
python3 .github/scripts/check_toc.py README.md
```

Expected: unittest `OK`; shellcheck silent; markdownlint `0 issues`; ToC check passes (no heading was added).

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/lint.yml .markdownlint-cli2.jsonc recovery/README.md README.md CLAUDE.md
git commit -m "recovery: CI, documentation and hardware checklist"
```
