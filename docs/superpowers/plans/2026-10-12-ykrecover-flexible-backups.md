# ykrecover Flexible Backups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Both recovery twins (`recovery/ykrecover` Python and `recovery/recoverCard.sh` bash) load backups by content, in any layout: any-name armored or binary key files, nested folders, split files, unprotected keys and GnuPG 1.x homes. They also renew an expired main key and handle keys with a missing card slot. Their `--dry-run` plans stay byte-identical.

**Architecture:**
- **Inventory:** walks the backup folder, sniffs key files and classifies each with `gpg --list-packets`. It groups the parts by main-key keyid, picks the files that supply the real secrets, and builds one candidate per recoverable key (plus one per GnuPG 2.x home).
- **Mode and card steps:** gain three rules: the main-key expiry rule, missing slots (`None` targets) and unprotected keys (no passphrase line for `keytocard`).
- **Order of work:** Python goes first and its goldens are generated with `UPDATE_GOLDEN=1`. Bash is then made to match.

**Tech Stack:** Python 3.12+ stdlib only; bash 5 with GNU coreutils, awk and date; GnuPG 2.2+ (tested with 2.4); Tkinter.

**Spec:** `docs/superpowers/specs/2026-10-12-ykrecover-flexible-backups-design.md`

## Global Constraints

- Golden plans must stay byte-identical for every existing case in `recovery/tests/test_plan_parity.py`. Do not regenerate existing fixtures. `make_fixtures.sh` must build only the profiles named on its command line.
- Python changes come first. Regenerate goldens with `UPDATE_GOLDEN=1` (Python only), then make bash match. Never hand-edit a golden.
- Stdlib only in Python. Platform differences live only in `recovery/ykrecover/osdeps.py`. Call `osdeps.run()` or `Home.run()`, never `subprocess`.
- Bash: `shellcheck --severity=error` must stay clean. Do not add new SC2155/SC2086 warnings.
- The inventory report and all warnings and errors go to **stderr**. Only plans go to stdout.
- Secrets never appear in argv, only on stdin.
- Commits: short lowercase imperative messages prefixed `recovery:`, or `docs:` for docs. **No `Co-Authored-By` or other attribution lines.**
- Test commands (run from `recovery/`):
  - full suite: `DISPLAY=:0 YKRECOVER_REQUIRE_GUI=1 python3 -m unittest discover -s tests -t .`
  - one test: `python3 -m unittest tests.test_backup.InventoryTests -v`
- The local WSLg display can drop ("X connection to :0 broken"). Rerun once before treating a GUI failure as real.

### Rulings made while planning (deviations from the spec text)

1. **Report lines are not aligned.** The format is `  <path>: <description> (<status>)`. Python pads by character and bash `printf %-*s` pads by byte, so aligned columns would break parity on non-ASCII paths.
2. **`describe()` wording does not depend on the mode.** The GUI shows it before the mode page. A missing slot is shown as `  [A] none (clone leaves it empty; renew and rotate create one)`.
3. **One candidate per key across the whole tree.** So `tests/fixtures/ed25519/` now yields `["export", "gnupghome"]`: the subkeys-only file holds the same key and is "not needed". Update `test_parent_folder_needs_a_choice` to match.
4. **Session options for the GUI.** For key candidates the GUI passes the user's root folder plus `key=candidate.key_id` and `source=candidate.kind`, not `candidate.path`. Split-file keys span folders.
5. **Unprotected keys drop `<CERTIFY_PASS>` from `keytocard` stdin, and from the revoke `--edit-key` stdin.** gpg asks no passphrase for an unprotected key, so the empty line would be read as the Admin PIN.
6. **The ambiguous-candidate message** becomes `several backups found; narrow --backup or pass --source or --key:`. Listing lines are `  <kind>: <path> (key <KEYID>)`, and `(key …)` is omitted for GnuPG homes.

## Review Focus

1. **Windows/OneDrive files:** armored files with a UTF-8 BOM and CRLF line endings, as saved by Windows editors or OneDrive, must be detected as key files. *Test: Task 2 `test_bom_and_crlf_armor_is_a_key_file`.*
2. **Spaces in paths:** a backup path containing spaces (`OneDrive - Company`) must work in both twins. *The fixture folder `flexible/asc backup` has a space, and the goldens in Task 7 use it.*
3. **The same key in two folders:** full secrets in one folder and subkeys-only in another must give one candidate built from the full file, not "several backups". *Test: Task 3 `test_same_key_in_two_folders_is_one_candidate`.*
4. **`--expire never`:**
   - `--expire 0`, `never` or `none` with a main key that has an expiry must extend the main key, to never.
   - A main key without an expiry is never touched.

   *Test: Task 4 `test_main_key_rule`.*
5. **Several keys:**
   - A file with two keys must give two candidates, and `--key` picks one.
   - Without `--key`, the error lists both keys.

   *Test: Task 3 `test_multi_key_file_gives_one_candidate_per_key`, and the Task 9 contract test.*

---

## File Structure

| File | Responsibility | Tasks |
| - | - | - |
| `recovery/tests/fixtures/make_stubs.py` (new) | Rewrites a binary secret-key export into gnu-dummy / divert-to-card stubs and armors it. Stdlib only. | 1 |
| `recovery/tests/fixtures/make_fixtures.sh` | Builds only the named profiles; adds the new profiles | 1 |
| `recovery/ykrecover/backup.py` | Packet parsing, key-file sniffing, walk, inventory, selection, report; `Candidate` and `KeyBundle` gain fields | 2, 3 |
| `recovery/ykrecover/modes.py` | Expire parsing, main-key rule, missing-slot targets, renew add steps, unprotected stdin | 4, 5, 6 |
| `recovery/ykrecover/card.py` | Card and verify steps skip empty slots; unprotected `keytocard` stdin | 5, 6 |
| `recovery/ykrecover/migrate.py` | Session uses the inventory, prints the report, validates, handles unprotected keys | 3, 4, 5, 6 |
| `recovery/ykrecover/cli.py` | Nothing beyond what `run()` needs | — |
| `recovery/ykrecover/gui.py` | Report on the backup page, unprotected passphrase field, candidate options | 10 |
| `recovery/recoverCard.sh` | Bash twin of all the above | 8, 9 |
| `recovery/tests/test_backup.py` | Parser, sniffing, inventory and selection tests | 2, 3 |
| `recovery/tests/test_modes.py` (new) | Expiry rule and missing-slot step tests | 4, 5, 6 |
| `recovery/tests/test_plan_parity.py` | New golden cases | 7 |
| `recovery/tests/test_inventory_parity.py` (new) | The report text is identical in both twins | 8 |
| `recovery/tests/test_cli_contract.py` | New error and warning messages, both twins | 9 |
| `recovery/tests/test_gui.py` | Report display, unprotected field, layout fit | 10 |
| `recovery/README.md` | "What counts as a backup", the report, renew behaviour | 11 |

---

### Task 1: Fixtures for the new backup shapes

**Files:**
- Create: `recovery/tests/fixtures/make_stubs.py`
- Modify: `recovery/tests/fixtures/make_fixtures.sh`
- Create (generated, committed): `recovery/tests/fixtures/{flexible,noauth,split,unprotected,gnupg1,multikey}/…`
- Test: `recovery/tests/test_fixtures.py`

**Interfaces:**
- Produces fixture folders (all passphrase `test-certify-pass` unless noted, each with `key.txt` like the existing profiles):
  - `flexible/asc backup/`: the spec's sample layout. Main key rsa2048 `cert,sign` expiring 2y, `[E]` rsa2048 2y and `[S]` rsa2048 2y, no `[A]`, all made at faked time 2020-01-01 so all of it is expired. Files:
    - `private/flex.priv.asc` (full)
    - `subkeys/flex.sub_priv.asc` (`--export-secret-subkeys`)
    - `stub/flex.priv_stub.asc` (all parts card stubs)
    - `stub/flex.sub_priv_stub.asc` (main dummy, subs card stubs)
    - `pub/flex.pub.asc`
    - `FLEX.rev` (copied from `openpgp-revocs.d`)
  - `noauth/export/`: ed25519 main `cert never`, `[S]` ed25519 and `[E]` cv25519 expiring 2099-12-31, no `[A]`. Guide file names `secret-<KID>-Certify.key` and `secret-<KID>-Subkeys.key`, plus `public-<KID>-2026-10-11.asc`.
  - `split/`: ed25519 main `cert never` plus 3 subkeys expiring 2099-12-31:
    - `a-main/main-only.asc`: main secret, subkeys gnu-dummy;
    - `b-subs/subs-only.asc`: `--export-secret-subkeys`.
  - `unprotected/`: ed25519 `cert never` plus 3 subkeys 2099-12-31, **empty passphrase**. File `key.asc` holds `--export-secret-keys`.
  - `gnupg1/`: ed25519 `cert never` plus 3 subkeys 2099-12-31. Files:
    - `secring.gpg`: binary `--export-secret-keys`;
    - `pubring.gpg`: binary `--export`.
  - `multikey/keys.asc`: two ed25519 keys, each with 3 subkeys, both in one armored `--export-secret-keys`. `key.txt` holds `KEY_FP=` and `KEY2_FP=`.

- [ ] **Step 1: Write `make_stubs.py`**

```python
#!/usr/bin/env python3
"""Turns a binary OpenPGP secret-key export into stub keys, as gpg writes them, and armors the result.

  make_stubs.py --main keep|dummy|card --sub keep|dummy|card IN OUT

keep  leaves the secret part as it is
dummy replaces it with a GNU gnu-dummy S2K (secret missing, as --export-secret-subkeys writes for the main key)
card  replaces it with a GNU divert-to-card S2K (the key lives on a smart card)
Only test fixtures use this; card stubs cannot be made with gpg alone without a card.
"""

import argparse
import base64
import sys

SECRET_KEY, SECRET_SUBKEY = 5, 7
SERIAL = bytes.fromhex("D2760001240103040006123456780000")


def read_packets(data: bytes):
    i = 0
    while i < len(data):
        first = data[i]
        i += 1
        if first & 0x40:  # new format
            tag, size = first & 0x3F, data[i]
            i += 1
            if size < 192:
                length = size
            elif size < 224:
                length = ((size - 192) << 8) + data[i] + 192
                i += 1
            elif size == 255:
                length = int.from_bytes(data[i:i + 4], "big")
                i += 4
            else:
                raise ValueError("partial body lengths do not occur in key exports")
        else:  # old format
            tag, kind = (first >> 2) & 0x0F, first & 3
            width = {0: 1, 1: 2, 2: 4}[kind]
            length = int.from_bytes(data[i:i + width], "big")
            i += width
        yield tag, data[i:i + length]
        i += length


def write_packet(tag: int, body: bytes) -> bytes:
    n = len(body)
    if n < 192:
        header = bytes([n])
    elif n < 8384:
        header = bytes([((n - 192) >> 8) + 192, (n - 192) & 0xFF])
    else:
        header = b"\xff" + n.to_bytes(4, "big")
    return bytes([0xC0 | tag]) + header + body


def mpi_size(body: bytes, offset: int) -> int:
    bits = int.from_bytes(body[offset:offset + 2], "big")
    return 2 + (bits + 7) // 8


def public_end(body: bytes) -> int:
    """Offset where the secret part of a v4 secret-key packet starts."""
    if body[0] != 4:
        raise ValueError(f"key packet version {body[0]} is not supported")
    algo, offset = body[5], 6
    counts = {1: 2, 2: 2, 3: 2, 16: 3, 17: 4, 18: 1, 19: 1, 22: 1}
    if algo not in counts:
        raise ValueError(f"public-key algorithm {algo} is not supported")
    if algo in (18, 19, 22):  # curve OID
        offset += 1 + body[offset]
    for _ in range(counts[algo]):
        offset += mpi_size(body, offset)
    if algo == 18:  # ECDH KDF parameters
        offset += 1 + body[offset]
    return offset


def stub(body: bytes, how: str) -> bytes:
    if how == "keep":
        return body
    tail = b"\xff\x00\x65\x00GNU"  # S2K usage 255, cipher 0, S2K type 101, hash 0, "GNU"
    tail += b"\x01" if how == "dummy" else b"\x02" + bytes([len(SERIAL)]) + SERIAL
    return body[:public_end(body)] + tail


def crc24(data: bytes) -> int:
    crc = 0xB704CE
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF


def armor(data: bytes) -> str:
    text = base64.b64encode(data).decode()
    lines = [text[i:i + 64] for i in range(0, len(text), 64)]
    check = base64.b64encode(crc24(data).to_bytes(3, "big")).decode()
    return ("-----BEGIN PGP PRIVATE KEY BLOCK-----\n\n" + "\n".join(lines) + f"\n={check}\n"
            "-----END PGP PRIVATE KEY BLOCK-----\n")


def main(argv) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--main", choices=("keep", "dummy", "card"), required=True)
    p.add_argument("--sub", choices=("keep", "dummy", "card"), required=True)
    p.add_argument("source")
    p.add_argument("target")
    args = p.parse_args(argv)
    with open(args.source, "rb") as f:
        data = f.read()
    out = b""
    for tag, body in read_packets(data):
        if tag == SECRET_KEY:
            body = stub(body, args.main)
        elif tag == SECRET_SUBKEY:
            body = stub(body, args.sub)
        out += write_packet(tag, body)
    with open(args.target, "w", encoding="ascii") as f:
        f.write(armor(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 2: Make `make_fixtures.sh` build only the profiles named on the command line, and add the new profiles**

Replace the three `make_profile …` lines at the end of the script with the code below. The new functions go below `make_profile`.

```bash
gen_key() { # gen_key UID PRIMARY_ALGO PRIMARY_USAGE PRIMARY_EXPIRY PASS [FAKED_TIME] → prints the fingerprint
  local -a time_opt=()
  [[ -z ${6:-} ]] || time_opt=(--faked-system-time "$6")
  printf '%s\n' "$5" | gpg --batch "${time_opt[@]}" --pinentry-mode loopback --passphrase-fd 0 \
    --quick-generate-key "$1" "$2" "$3" "$4" >/dev/null 2>&1
  gpg --with-colons --list-keys "$1" | awk -F: '/^fpr/ {print $10; exit}'
}

add_sub() { # add_sub FP ALGO USAGE EXPIRY PASS [FAKED_TIME]
  local -a time_opt=()
  [[ -z ${6:-} ]] || time_opt=(--faked-system-time "$6")
  printf '%s\n' "$5" | gpg --batch "${time_opt[@]}" --pinentry-mode loopback --passphrase-fd 0 \
    --quick-add-key "$1" "$2" "$3" "$4"
}

export_secret() { # export_secret FP PASS OUT [--armor] [--export-secret-subkeys]
  local fp=$1 pass=$2 out=$3
  shift 3
  local what=--export-secret-keys opts=()
  for a in "$@"; do
    case $a in --armor) opts+=(--armor) ;; --export-secret-subkeys) what=$a ;; esac
  done
  printf '%s\n' "$pass" | gpg --batch --pinentry-mode loopback --passphrase-fd 0 "${opts[@]}" --yes \
    --output "$out" "$what" "$fp"
}

write_info() { # write_info FP DIR
  {
    printf 'KEY_FP=%s\nKEY_ID=%s\n' "$1" "${1: -16}"
    gpg --with-colons --list-keys "$1" |
      awk -F: '$1 == "sub" {u = $12} $1 == "fpr" && u {print "FPR_" toupper(u) "=" $10; u = ""}'
  } >"$2/key.txt"
}

fresh_home() {
  GNUPGHOME=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/ykfix-XXXXXX")
  export GNUPGHOME
}

done_home() { gpgconf --kill all; rm -rf "$GNUPGHOME"; }

make_flexible() { # the spec's sample layout: expired rsa2048 [SC] main, [E] and [S], no [A], .asc names in subfolders
  local dir="flexible/asc backup" fp kid tmp
  fresh_home
  rm -rf flexible
  mkdir -p "$dir/private" "$dir/subkeys" "$dir/stub" "$dir/pub"
  fp=$(gen_key "Recovery Test (flexible) <test@example.org>" rsa2048 cert,sign 2y "$PASS" 20200101T000000)
  add_sub "$fp" rsa2048 encrypt 2y "$PASS" 20200101T000000
  add_sub "$fp" rsa2048 sign 2y "$PASS" 20200101T000000
  kid=${fp: -16}
  export_secret "$fp" "$PASS" "$dir/private/flex.priv.asc" --armor
  export_secret "$fp" "$PASS" "$dir/subkeys/flex.sub_priv.asc" --armor --export-secret-subkeys
  tmp=$(mktemp "$GNUPGHOME/x-XXXXXX")
  export_secret "$fp" "$PASS" "$tmp"
  python3 make_stubs.py --main card --sub card "$tmp" "$dir/stub/flex.priv_stub.asc"
  export_secret "$fp" "$PASS" "$tmp" --export-secret-subkeys
  python3 make_stubs.py --main keep --sub card "$tmp" "$dir/stub/flex.sub_priv_stub.asc"
  gpg --batch --armor --output "$dir/pub/flex.pub.asc" --export "$fp"
  cp "$GNUPGHOME/openpgp-revocs.d/$fp.rev" "$dir/FLEX.rev"
  write_info "$fp" flexible
  printf 'KID=%s\n' "$kid" >>flexible/key.txt
  done_home
}

make_noauth() {
  local fp kid
  fresh_home
  rm -rf noauth
  mkdir -p noauth/export
  fp=$(gen_key "Recovery Test (noauth) <test@example.org>" ed25519 cert never "$PASS")
  add_sub "$fp" ed25519 sign 2099-12-31 "$PASS"
  add_sub "$fp" cv25519 encrypt 2099-12-31 "$PASS"
  kid=${fp: -16}
  export_secret "$fp" "$PASS" "noauth/export/secret-$kid-Certify.key" --armor
  export_secret "$fp" "$PASS" "noauth/export/secret-$kid-Subkeys.key" --armor --export-secret-subkeys
  gpg --batch --armor --output "noauth/export/public-$kid-2026-10-11.asc" --export "$fp"
  write_info "$fp" noauth
  done_home
}

three_subs() { # three_subs FP PASS
  add_sub "$1" ed25519 sign 2099-12-31 "$2"
  add_sub "$1" cv25519 encrypt 2099-12-31 "$2"
  add_sub "$1" ed25519 auth 2099-12-31 "$2"
}

make_split() { # main key secret in one file, subkeys secret in another
  local fp tmp
  fresh_home
  rm -rf split
  mkdir -p split/a-main split/b-subs
  fp=$(gen_key "Recovery Test (split) <test@example.org>" ed25519 cert never "$PASS")
  three_subs "$fp" "$PASS"
  tmp=$(mktemp "$GNUPGHOME/x-XXXXXX")
  export_secret "$fp" "$PASS" "$tmp"
  python3 make_stubs.py --main keep --sub dummy "$tmp" split/a-main/main-only.asc
  export_secret "$fp" "$PASS" split/b-subs/subs-only.asc --armor --export-secret-subkeys
  write_info "$fp" split
  done_home
}

make_unprotected() {
  local fp
  fresh_home
  rm -rf unprotected
  mkdir -p unprotected
  fp=$(gen_key "Recovery Test (unprotected) <test@example.org>" ed25519 cert never "")
  three_subs "$fp" ""
  export_secret "$fp" "" unprotected/key.asc --armor
  write_info "$fp" unprotected
  done_home
}

make_gnupg1() { # a GnuPG 1.x home: secring.gpg is a binary transferable secret key, as GnuPG 1 stored it
  local fp
  fresh_home
  rm -rf gnupg1
  mkdir -p gnupg1
  fp=$(gen_key "Recovery Test (gnupg1) <test@example.org>" ed25519 cert never "$PASS")
  three_subs "$fp" "$PASS"
  export_secret "$fp" "$PASS" gnupg1/secring.gpg
  gpg --batch --yes --output gnupg1/pubring.gpg --export "$fp"
  write_info "$fp" gnupg1
  done_home
}

make_multikey() { # two keys in one armored file
  local fp1 fp2
  fresh_home
  rm -rf multikey
  mkdir -p multikey
  fp1=$(gen_key "Recovery Test (multi one) <one@example.org>" ed25519 cert never "$PASS")
  three_subs "$fp1" "$PASS"
  fp2=$(gen_key "Recovery Test (multi two) <two@example.org>" ed25519 cert never "$PASS")
  three_subs "$fp2" "$PASS"
  printf '%s\n' "$PASS" | gpg --batch --pinentry-mode loopback --passphrase-fd 0 --armor --yes \
    --output multikey/keys.asc --export-secret-keys "$fp1" "$fp2"
  printf 'KEY_FP=%s\nKEY2_FP=%s\n' "$fp1" "$fp2" >multikey/key.txt
  done_home
}

build() {
  case $1 in
    ed25519) make_profile ed25519 ed25519 "ed25519 cv25519 ed25519" 2099-12-31 ;;
    rsa4096) make_profile rsa4096 rsa4096 "rsa4096 rsa4096 rsa4096" 2099-12-31 ;;
    expired) make_profile expired ed25519 "ed25519 cv25519 ed25519" 1y 20200101T000000 ;;
    flexible) make_flexible ;;
    noauth) make_noauth ;;
    split) make_split ;;
    unprotected) make_unprotected ;;
    gnupg1) make_gnupg1 ;;
    multikey) make_multikey ;;
    *) printf 'unknown profile: %s\n' "$1" >&2; exit 2 ;;
  esac
}

# Usage: make_fixtures.sh [PROFILE...]; without arguments every profile is rebuilt (this changes the goldens).
if (($# == 0)); then
  set -- ed25519 rsa4096 expired flexible noauth split unprotected gnupg1 multikey
fi
for profile in "$@"; do build "$profile"; done
```

Also update the header comment: "Rerun only for the profiles you need, e.g. `./make_fixtures.sh flexible`; rebuilding ed25519, rsa4096 or expired changes the golden plans."

- [ ] **Step 3: Build only the new profiles**

Run: `cd recovery/tests/fixtures && ./make_fixtures.sh flexible noauth split unprotected gnupg1 multikey && git status --short .`

Expected: new folders only. `git diff --stat -- ed25519 rsa4096 expired` must be empty.

- [ ] **Step 4: Add a fixture-shape test to `recovery/tests/test_fixtures.py`**

Read the file first and match its existing style. Add:

```python
class NewFixtureShapeTests(unittest.TestCase):
    def packets(self, path):
        result = subprocess.run([REAL_GPG, "--batch", "--list-packets", str(path)], capture_output=True, text=True,
                                env={**os.environ, "GNUPGHOME": str(short_tempdir(self, "pk"))}, check=False)
        return result.stdout

    def test_stub_files_are_card_stubs(self):
        folder = FIXTURES / "flexible" / "asc backup" / "stub"
        full = self.packets(folder / "flex.priv_stub.asc")
        self.assertEqual(full.count("gnu-divert-to-card"), 3)
        subs = self.packets(folder / "flex.sub_priv_stub.asc")
        self.assertEqual(subs.count("gnu-divert-to-card"), 2)
        self.assertEqual(subs.count("gnu-dummy"), 1)

    def test_split_main_file_has_dummy_subkeys(self):
        text = self.packets(FIXTURES / "split" / "a-main" / "main-only.asc")
        self.assertEqual(text.count("gnu-dummy"), 3)
        self.assertEqual(text.count("S2K"), 1)

    def test_unprotected_key_has_no_s2k(self):
        text = self.packets(FIXTURES / "unprotected" / "key.asc")
        self.assertNotIn("S2K", text)
        self.assertIn("bits]", text)
```

Use whatever imports the file already has, and add `os`, `subprocess`, `REAL_GPG`, `short_tempdir` and `FIXTURES` from `tests.helpers` as needed.

- [ ] **Step 5: Run the tests**

Run: `cd recovery && python3 -m unittest tests.test_fixtures -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add recovery/tests/fixtures recovery/tests/test_fixtures.py
git commit -m "recovery: fixtures for non-standard backup layouts"
```

---

### Task 2: Packet parser, key-file sniffing and the folder walk (Python)

**Files:**
- Modify: `recovery/ykrecover/backup.py`
- Test: `recovery/tests/test_backup.py`

**Interfaces:**
- Produces in `backup.py`:
  - constants: `STATE_WORDS: dict`, `RANK: dict`, `MAX_KEY_FILE = 1 << 20`, `MAX_DEPTH = 3`
  - `@dataclass(frozen=True) class Part: main_keyid: str; keyid: str; role: str; state: str`
  - `parse_packets(text: str) -> list[Part]`
  - `looks_like_key_file(path: Path) -> bool`
  - `walk(folder: Path) -> tuple[list[Candidate], list[Path]]`: the GnuPG 2.x homes (as `gnupghome` Candidates, built exactly as `_detect_dir` builds them today) and the key files, in walk order.

- [ ] **Step 1: Write failing tests**

Add to `recovery/tests/test_backup.py`:

```python
LIST_PACKETS = """\
# off=0 ctb=95 tag=5 hlen=3 plen=966
:secret key packet:
\tversion 4, algo 1, created 1600000000, expires 0
\tpkey[0]: [2048 bits]
\tpkey[1]: [17 bits]
\titer+salt S2K, algo: 7, SHA1 protection, hash: 2, salt: 0011223344556677
\tprotect count: 41943040 (244)
\tprotect IV:  00 01 02 03 04 05 06 07 08 09 0a 0b 0c 0d 0e 0f
\tskey[2]: [v4 protected]
\tkeyid: 0123456789ABCDEF
:user ID packet: "Test <t@example.org>"
:signature packet: algo 1, keyid 0123456789ABCDEF
:secret sub key packet:
\tversion 4, algo 1, created 1600000000, expires 0
\tgnu-dummy, algo: 0, simple checksum, hash: 0
\tprotect IV: 
\tkeyid: AAAA1111BBBB2222
:secret sub key packet:
\tversion 4, algo 1, created 1600000400, expires 0
\tgnu-divert-to-card, algo: 0, simple checksum, hash: 0
\tkeyid: CCCC3333DDDD4444
:secret sub key packet:
\tversion 4, algo 22, created 1600000400, expires 0
\tskey[3]: [255 bits]
\tkeyid: EEEE5555FFFF6666
:public sub key packet:
\tversion 4, algo 1, created 1600000400, expires 0
\tkeyid: 1111222233334444
"""


class PacketParseTests(unittest.TestCase):
    def test_states(self):
        parts = backup.parse_packets(LIST_PACKETS)
        self.assertEqual([(p.main_keyid, p.keyid, p.role, p.state) for p in parts], [
            ("0123456789ABCDEF", "0123456789ABCDEF", "main", "secret"),
            ("0123456789ABCDEF", "AAAA1111BBBB2222", "sub", "missing"),
            ("0123456789ABCDEF", "CCCC3333DDDD4444", "sub", "card-stub"),
            ("0123456789ABCDEF", "EEEE5555FFFF6666", "sub", "secret-unprotected"),
            ("0123456789ABCDEF", "1111222233334444", "sub", "public"),
        ])

    def test_no_key_packets(self):
        self.assertEqual(backup.parse_packets(":signature packet: algo 1, keyid 0123456789ABCDEF\n"), [])


class SniffTests(unittest.TestCase):
    def write(self, name, data: bytes):
        path = short_tempdir(self, "sniff") / name
        path.write_bytes(data)
        return path

    def test_armored_private_and_public(self):
        self.assertTrue(backup.looks_like_key_file(self.write("a.asc", b"-----BEGIN PGP PRIVATE KEY BLOCK-----\n")))
        self.assertTrue(backup.looks_like_key_file(self.write("b.txt", b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n")))

    def test_bom_and_crlf_armor_is_a_key_file(self):
        data = b"\xef\xbb\xbf\r\n  -----BEGIN PGP PRIVATE KEY BLOCK-----\r\n\r\n"
        self.assertTrue(backup.looks_like_key_file(self.write("windows.asc", data)))

    def test_binary_packets(self):
        self.assertTrue(backup.looks_like_key_file(self.write("new.gpg", bytes([0xC5, 0x10]) + b"\x04" * 16)))
        self.assertTrue(backup.looks_like_key_file(self.write("old.gpg", bytes([0x95, 0x01, 0x00]))))
        self.assertTrue(backup.looks_like_key_file(self.write("pub.gpg", bytes([0x99, 0x01, 0x00]))))

    def test_other_files(self):
        self.assertFalse(backup.looks_like_key_file(self.write("sig.asc", b"-----BEGIN PGP SIGNATURE-----\n")))
        self.assertFalse(backup.looks_like_key_file(self.write("rev.rev", b"This is a revocation certificate\n")))
        self.assertFalse(backup.looks_like_key_file(self.write("kbx", b"\x00\x00\x00\x20\x01")))
        self.assertFalse(backup.looks_like_key_file(self.write("big.asc", b"-----BEGIN PGP PRIVATE KEY BLOCK-----"
                                                                           + b"x" * (1 << 20))))


class WalkTests(unittest.TestCase):
    def test_walk_finds_nested_files_and_skips_hidden_and_deep(self):
        root = short_tempdir(self, "walk")
        key = b"-----BEGIN PGP PRIVATE KEY BLOCK-----\n"
        for rel in ("top.asc", "a/one.asc", "a/b/c/three.asc", "a/b/c/d/four.asc", ".hidden/x.asc", "a/notes.txt"):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(key if rel.endswith(".asc") else b"notes")
        homes, files = backup.walk(root)
        self.assertEqual(homes, [])
        self.assertEqual([p.relative_to(root).as_posix() for p in files], ["a/b/c/three.asc", "a/one.asc", "top.asc"])

    def test_gnupg2_home_is_a_candidate_and_its_rings_are_not_key_files(self):
        homes, files = backup.walk(FIXTURES / "ed25519" / "gnupghome")
        self.assertEqual([h.kind for h in homes], ["gnupghome"])
        self.assertEqual(files, [])

    def test_gnupg1_home_contributes_its_rings_as_key_files(self):
        homes, files = backup.walk(FIXTURES / "gnupg1")
        self.assertEqual(homes, [])
        self.assertEqual([p.name for p in files], ["pubring.gpg", "secring.gpg"])
```

Add `short_tempdir` to the `tests.helpers` import at the top of the file.

- [ ] **Step 2: Run them to see them fail**

Run: `cd recovery && python3 -m unittest tests.test_backup -v`
Expected: FAIL with `AttributeError: module 'ykrecover.backup' has no attribute 'parse_packets'` (and the same for `looks_like_key_file` and `walk`).

- [ ] **Step 3: Implement in `backup.py`**

Add `import re` to the imports. Add the following below `KINDS`/`PUBRINGS`/`LIST_SECRET`:

```python
HOME_FILES = (*PUBRINGS, "trustdb.gpg")
KEY_PREFIXES = (b"-----BEGIN PGP PRIVATE KEY BLOCK-----", b"-----BEGIN PGP PUBLIC KEY BLOCK-----")
KEY_TAGS = (5, 6, 7, 14)  # secret key, public key, secret subkey, public subkey
MAX_KEY_FILE = 1 << 20
MAX_DEPTH = 3
RANK = {"secret": 2, "secret-unprotected": 1}  # anything else supplies no secret
STATE_WORDS = {"secret": "secret", "secret-unprotected": "secret (no passphrase)", "missing": "missing",
               "card-stub": "card stub", "public": "public"}
PACKET = re.compile(r"^:(secret|public)( sub)? key packet:")


@dataclass(frozen=True)
class Part:
    main_keyid: str
    keyid: str
    role: str  # main | sub
    state: str  # secret | secret-unprotected | missing | card-stub | public


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


def _is_gnupg2_home(folder: Path) -> bool:
    return (folder / "private-keys-v1.d").is_dir() and any((folder / n).is_file() for n in PUBRINGS)


def walk(folder: Path) -> tuple:
    """GnuPG 2.x homes and key files under folder (depth 3, sorted by byte order, hidden names skipped)."""
    homes, files = [], []

    def visit(current: Path, depth: int):
        home = _is_gnupg2_home(current)
        if home:
            names = ("private-keys-v1.d", *PUBRINGS, "trustdb.gpg")
            homes.append(Candidate("gnupghome", current, tuple(current / n for n in names if (current / n).exists())))
        try:
            entries = sorted(current.iterdir(), key=lambda p: os.fsencode(p.name))
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith(".") or entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name != "private-keys-v1.d" and depth < MAX_DEPTH:
                    visit(entry, depth + 1)
            elif not (home and entry.name in HOME_FILES) and looks_like_key_file(entry):
                files.append(entry)

    visit(folder, 0)
    return homes, files
```

- [ ] **Step 4: Run the tests**

Run: `cd recovery && python3 -m unittest tests.test_backup -v`
Expected: the new tests PASS and the existing ones still PASS (`detect` is not changed yet).

- [ ] **Step 5: Commit**

```bash
git add recovery/ykrecover/backup.py recovery/tests/test_backup.py
git commit -m "recovery: classify backup files by their packets"
```

---

### Task 3: Inventory, selection, report and loading (Python)

**Files:**
- Modify: `recovery/ykrecover/backup.py` (`Candidate`, `detect`, `choose`, `load`; new `FileReport`, `Inventory`, `inventory`, `report`)
- Modify: `recovery/ykrecover/migrate.py` (`Session.load`, `run`)
- Test: `recovery/tests/test_backup.py`

**Interfaces:**
- Consumes: `Part`, `parse_packets`, `walk`, `RANK`, `STATE_WORDS` (Task 2).
- Produces:
  - `Candidate(kind, path, files, key_id: str = "", unprotected: bool = False)`. The new fields have defaults, so existing constructions still work.
  - `@dataclass(frozen=True) class FileReport: path: Path; key: str; parts: tuple; status: str`. `key` is the main keyid, or `""` for an unreadable file. `status` is one of `used | not needed | ignored | unreadable`.
  - `@dataclass(frozen=True) class Inventory: root: Path; files: tuple; candidates: tuple; unrecoverable: tuple`. `unrecoverable` holds the main keyids that have no secret part.
  - `inventory(folder: Path) -> Inventory`. It raises `RecoveryError(f"backup folder not found: {folder}", EXIT_BACKUP)` if the folder is missing.
  - `detect(folder) -> list[Candidate]`, which is `list(inventory(folder).candidates)`.
  - `report(inv: Inventory) -> str`.
  - `choose(candidates, kind=None, key=None) -> Candidate`.
  - `Session.inventory: Inventory | None` and `Session.candidate: Candidate | None`.

**Exact report format** (both twins; the bash twin matches it in Task 8):

```text
Backup files in <root>:
  <relpath>: key <KEYID>: <description> (<status>)
  <relpath>: not a readable key file (ignored)
  key <KEYID>: not recoverable from this folder (card stubs / public key only)
```

- `<relpath>` uses `/` separators, relative to the root.
- A file holding several keys gets one line per key, in order of first appearance.
- `<description>` is `main <STATE_WORD>` if the file holds the main key. After that come subkey counts, grouped per state in the order secret, secret-unprotected, missing, card-stub, public, as `<n> subkey <STATE_WORD>` (1) or `<n> subkeys <STATE_WORD>` (more than 1). The pieces are joined with `, `.
- The `not recoverable` lines come after all file lines, in order of first appearance.
- If the walk found no key files, `report()` returns `""`.

**Selection algorithm** (per main keyid, keys in order of first appearance):
1. `rows` = the files holding parts of this key, each with its parts for this key.
2. Sort by `(-secret_count, -main_secret, walk_index)`. `secret_count` is the number of parts with `RANK > 0`, and `main_secret` is 1 if the main part has `RANK > 0`.
3. Go through the files in that order. A file is used if any of its parts has a higher `RANK` than the best already supplied for that part's keyid. When a file is used, record the supplied rank for each of its parts.
4. If nothing was used, the key is unrecoverable. Otherwise:
   - kind = `export` if the main keyid was supplied with `RANK > 0`, else `subkeys-only`;
   - `unprotected` = every supplied rank is 1;
   - path = parent folder of the first used file;
   - files = the used files in use order.
5. Status per (file, key):
   - `used` if the file was used;
   - else `not needed` if it has any part with `RANK > 0`;
   - else `ignored`.
6. Candidate order: `sorted(homes + keys, key=(os.fsencode(str(path)), KIND_ORDER[kind]))`, with `KIND_ORDER = {"gnupghome": 0, "export": 1, "subkeys-only": 2}`.

- [ ] **Step 1: Write failing tests**

```python
class InventoryTests(unittest.TestCase):
    def test_flexible_layout(self):
        root = FIXTURES / "flexible" / "asc backup"
        inv = backup.inventory(root)
        kid = fixture_info("flexible")["KEY_ID"]
        self.assertEqual([(c.kind, c.key_id, [f.name for f in c.files]) for c in inv.candidates],
                         [("export", kid, ["flex.priv.asc"])])
        self.assertEqual(backup.report(inv), "\n".join([
            f"Backup files in {root}:",
            f"  private/flex.priv.asc: key {kid}: main secret, 2 subkeys secret (used)",
            f"  pub/flex.pub.asc: key {kid}: main public, 2 subkeys public (ignored)",
            f"  stub/flex.priv_stub.asc: key {kid}: main card stub, 2 subkeys card stub (ignored)",
            f"  stub/flex.sub_priv_stub.asc: key {kid}: main missing, 2 subkeys card stub (ignored)",
            f"  subkeys/flex.sub_priv.asc: key {kid}: main missing, 2 subkeys secret (not needed)",
        ]))

    def test_guide_export_folder_is_unchanged(self):
        inv = backup.inventory(FIXTURES / "ed25519" / "export")
        (c,) = inv.candidates
        self.assertEqual((c.kind, [f.name for f in c.files]), ("export", [f"secret-{fixture_info('ed25519')['KEY_ID']}-Certify.key"]))
        self.assertEqual(c.path, FIXTURES / "ed25519" / "export")

    def test_same_key_in_two_folders_is_one_candidate(self):
        found = backup.detect(FIXTURES / "ed25519")
        self.assertEqual([c.kind for c in found], ["export", "gnupghome"])

    def test_split_files_use_both(self):
        (c,) = backup.detect(FIXTURES / "split")
        self.assertEqual((c.kind, [f.name for f in c.files]), ("export", ["subs-only.asc", "main-only.asc"]))

    def test_unprotected_candidate(self):
        (c,) = backup.detect(FIXTURES / "unprotected")
        self.assertTrue(c.unprotected)
        self.assertFalse(backup.detect(FIXTURES / "split")[0].unprotected)

    def test_gnupg1_home(self):
        (c,) = backup.detect(FIXTURES / "gnupg1")
        self.assertEqual((c.kind, [f.name for f in c.files]), ("export", ["secring.gpg"]))

    def test_multi_key_file_gives_one_candidate_per_key(self):
        info = fixture_info("multikey")
        found = backup.detect(FIXTURES / "multikey")
        self.assertEqual(sorted(c.key_id for c in found), sorted([info["KEY_FP"][-16:], info["KEY2_FP"][-16:]]))
        with self.assertRaises(RecoveryError) as ctx:
            backup.choose(found)
        self.assertIn("pass --source or --key", str(ctx.exception))
        self.assertEqual(backup.choose(found, key=info["KEY2_FP"]).key_id, info["KEY2_FP"][-16:])

    def test_only_stubs_is_unrecoverable(self):
        root = short_tempdir(self, "stubs")
        stub = FIXTURES / "flexible" / "asc backup" / "stub" / "flex.priv_stub.asc"
        (root / "stub.asc").write_bytes(stub.read_bytes())
        inv = backup.inventory(root)
        self.assertEqual(inv.candidates, ())
        self.assertIn("not recoverable from this folder (card stubs / public key only)", backup.report(inv))

    def test_unreadable_key_looking_file_is_reported(self):
        root = short_tempdir(self, "junk")
        (root / "junk.asc").write_bytes(b"-----BEGIN PGP PRIVATE KEY BLOCK-----\n\nnot base64 at all\n")
        (root / "key.asc").write_bytes((FIXTURES / "unprotected" / "key.asc").read_bytes())
        inv = backup.inventory(root)
        self.assertEqual(len(inv.candidates), 1)
        self.assertIn("  junk.asc: not a readable key file (ignored)", backup.report(inv))

    def test_loads_split_files_into_one_key(self):
        (c,) = backup.detect(FIXTURES / "split")
        staging = Home.create("t")
        self.addCleanup(staging.destroy)
        (bundle,) = backup.load(c, staging)
        self.assertTrue(bundle.has_certify_secret)
        self.assertTrue(all(k.secret for k in bundle.subkeys))
```

Change `test_parent_folder_needs_a_choice` (Ruling 3) to:

```python
    def test_parent_folder_needs_a_choice(self):
        found = backup.detect(FIXTURES / "ed25519")
        self.assertEqual([c.kind for c in found], ["export", "gnupghome"])
        with self.assertRaises(RecoveryError) as ctx:
            backup.choose(found)
        self.assertEqual(ctx.exception.code, EXIT_BACKUP)
        self.assertEqual(backup.choose(found, "gnupghome").kind, "gnupghome")
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd recovery && python3 -m unittest tests.test_backup -v`
Expected: FAIL. `inventory` is missing, and `detect` still returns three candidates for the parent folder.

- [ ] **Step 3: Implement in `backup.py`**

Replace `Candidate`, `_detect_dir`, `detect` and `choose` with the code below, and keep the rest of the file:

```python
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


def classify(path: Path, scan: Home):
    result = scan.run(["gpg", "--batch", "--list-packets", str(path)])
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


def choose(candidates, kind=None, key=None) -> Candidate:
    if kind:
        candidates = [c for c in candidates if c.kind == kind]
    if key:
        want = key.upper().replace(" ", "")
        candidates = [c for c in candidates if not c.key_id or want.endswith(c.key_id) or c.key_id.endswith(want)]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise RecoveryError("no backup found (expected secret-*-Certify.key, secret-*-Subkeys.key "
                            "or a GnuPG home)", EXIT_BACKUP)
    listing = "\n".join(f"  {c.kind}: {c.path}" + (f" (key {c.key_id})" if c.key_id else "") for c in candidates)
    raise RecoveryError(f"several backups found; narrow --backup or pass --source or --key:\n{listing}", EXIT_BACKUP)
```

Note: `Home` is already imported. The scan home must be created only when there are key files, because `Home.create` needs a work base.

- [ ] **Step 4: Use the inventory in `migrate.py`**

In `Session.__init__`, add `self.inventory = None` and `self.candidate = None`. Replace `Session.load` with:

```python
    def load(self) -> backup.KeyBundle:
        inv = backup.inventory(Path(self.opts.backup))
        self.inventory = inv
        if not inv.candidates:
            if inv.files:
                raise RecoveryError(f"no secret keys in {inv.root}: only card stubs or public keys", EXIT_BACKUP)
            raise RecoveryError(f"no backup found in {inv.root} (looked for OpenPGP key files and GnuPG homes)",
                                EXIT_BACKUP)
        candidate = backup.choose(inv.candidates, self.opts.source or None, self.opts.key or None)
        self.candidate = candidate
        if self.staging:
            self._destroy(self.staging)
        self.staging = Home.create("staging")
        self.bundle = backup.choose_key(backup.load(candidate, self.staging), self.opts.key or candidate.key_id or None)
        self.targets = modes.select_targets(self.bundle)
        return self.bundle
```

In `run()`, replace `session.load()` with:

```python
        try:
            session.load()
        finally:
            text = backup.report(session.inventory) if session.inventory else ""
            if text:
                ui.info(text)
```

Import `backup` in `migrate.py` if it isn't already imported (it is, as `backup`), and import `Path` if needed.

- [ ] **Step 5: Run the backup tests and the whole suite**

Run: `cd recovery && python3 -m unittest tests.test_backup -v && DISPLAY=:0 YKRECOVER_REQUIRE_GUI=1 python3 -m unittest discover -s tests -t .`

Expected: PASS. If a test expects the old "no backup found (expected …)" text for an **empty** folder, update it to the new `no backup found in <folder> (looked for OpenPGP key files and GnuPG homes)` text. The bash twin still prints the old text until Task 8, so a contract test that compares both twins on that message can be marked `@unittest.expectedFailure` for bash. Remove that marker in Task 8.

- [ ] **Step 6: Check that the existing goldens are unchanged**

Run: `cd recovery && python3 -m unittest tests.test_plan_parity.PythonPlanParityTests -v && git diff --stat -- tests/golden`
Expected: PASS, with an empty diff.

- [ ] **Step 7: Commit**

```bash
git add recovery/ykrecover/backup.py recovery/ykrecover/migrate.py recovery/tests
git commit -m "recovery: choose backup files by content and report what each holds"
```

---

### Task 4: Main-key expiry (Python)

**Files:**
- Modify: `recovery/ykrecover/backup.py` (`KeyBundle`, `parse_secret_keys`)
- Modify: `recovery/ykrecover/modes.py`
- Modify: `recovery/ykrecover/migrate.py` (`validate_bundle`, `staging_steps`)
- Create: `recovery/tests/test_modes.py`

**Interfaces:**
- Produces:
  - `KeyBundle(..., expires: int = 0, algo: str = "")`, read from the `sec` line: field 7 and field 17 (or `rsa<field 3>`).
  - `modes.expire_at(expire: str, now: int) -> int | None`.
  - `modes.main_needs_extending(bundle, expire: str, now: int) -> bool`.
  - `modes.mode_steps(mode, bundle, targets, expire, algo, revoke_old, now: int = 0, unprotected: bool = False)`. Tasks 5 and 6 fill in the last two parameters.

- [ ] **Step 1: Write failing tests (`recovery/tests/test_modes.py`)**

```python
import unittest

from ykrecover import modes
from ykrecover.backup import KeyBundle, Subkey

NOW = 1_800_000_000
DAY = 86400


def bundle(expires=0, subkeys=None, algo="ed25519"):
    subs = subkeys if subkeys is not None else (
        Subkey("S" * 40, "", "s", "ed25519", 1, 0, "u"), Subkey("E" * 40, "", "e", "cv25519", 1, 0, "u"),
        Subkey("A" * 40, "", "a", "ed25519", 1, 0, "u"))
    return KeyBundle("F" * 40, "F" * 16, ("T",), tuple(subs), True, expires, algo)


class ExpireTests(unittest.TestCase):
    def test_expire_at(self):
        self.assertEqual(modes.expire_at("2y", NOW), NOW + 730 * DAY)
        self.assertEqual(modes.expire_at("10", NOW), NOW + 10 * DAY)
        self.assertEqual(modes.expire_at("3w", NOW), NOW + 21 * DAY)
        self.assertEqual(modes.expire_at("1m", NOW), NOW + 30 * DAY)
        self.assertEqual(modes.expire_at("2099-12-31", NOW), 4102358400)
        for never in ("0", "never", "none", "NEVER", "junk", "2023-02-30"):
            self.assertIsNone(modes.expire_at(never, NOW), never)

    def test_main_key_rule(self):
        self.assertFalse(modes.main_needs_extending(bundle(0), "2y", NOW))  # never expires: untouched
        self.assertFalse(modes.main_needs_extending(bundle(0), "never", NOW))
        self.assertTrue(modes.main_needs_extending(bundle(NOW - 1), "2y", NOW))  # expired
        self.assertTrue(modes.main_needs_extending(bundle(NOW + DAY), "2y", NOW))  # before the subkeys
        self.assertFalse(modes.main_needs_extending(bundle(NOW + 1000 * DAY), "2y", NOW))
        self.assertTrue(modes.main_needs_extending(bundle(NOW + 1000 * DAY), "never", NOW))

    def test_renew_extends_main_first(self):
        steps = modes.mode_steps("renew", bundle(NOW - 1), {"s": "S" * 40, "e": "E" * 40, "a": "A" * 40},
                                 "2y", "ed25519", False, NOW)
        self.assertEqual(steps[0].argv[-3:], ("--quick-set-expire", "F" * 40, "2y"))
        self.assertEqual(steps[1].argv[-6:-3], ("--quick-set-expire", "F" * 40, "2y"))
        self.assertEqual(len(steps), 2)

    def test_rotate_extends_main_first(self):
        steps = modes.mode_steps("rotate", bundle(NOW - 1), {"s": "S" * 40, "e": "E" * 40, "a": "A" * 40},
                                 "2y", "ed25519", False, NOW)
        self.assertEqual(steps[0].argv[-3:], ("--quick-set-expire", "F" * 40, "2y"))
        self.assertIn("--quick-add-key", steps[1].argv)
```

Add a parse test to `tests/test_backup.py` in `ParseTests`:

```python
    def test_main_key_expiry_and_algo(self):
        colons = COLONS_TWO_KEYS.replace("sec:u:255:22:D35238B02045AC9D:1791717022:::", 
                                         "sec:e:255:22:D35238B02045AC9D:1791717022:1791800000::", 1)
        first, second = backup.parse_secret_keys(colons)
        self.assertEqual((first.expires, first.algo), (1791800000, "ed25519"))
        self.assertEqual(second.expires, 0)
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd recovery && python3 -m unittest tests.test_modes tests.test_backup -v`
Expected: FAIL (`expire_at` is missing, and `KeyBundle` takes too many arguments).

- [ ] **Step 3: Implement**

`backup.py`:
- `KeyBundle` gets `expires: int = 0` and `algo: str = ""` after `has_certify_secret`.
- In `parse_secret_keys`, the `sec` branch stores `"expires": int(f[6] or 0), "algo": f[16] or f"rsa{f[2]}"`.
- The final list comprehension passes `b["expires"], b["algo"]`.

`modes.py`:

```python
import datetime
import re

UNIT_SECONDS = {"": 86400, "d": 86400, "w": 7 * 86400, "m": 30 * 86400, "y": 365 * 86400}


def expire_at(expire: str, now: int):
    """When subkeys given this --expire end; None for never, or for a form we do not parse."""
    text = expire.strip().lower()
    match = re.fullmatch(r"(\d+)([dwmy]?)", text)
    if match and int(match.group(1)) > 0:
        return now + int(match.group(1)) * UNIT_SECONDS[match.group(2)]
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        try:
            day = datetime.datetime(*map(int, match.groups()), tzinfo=datetime.timezone.utc)
        except ValueError:
            return None
        return int(day.timestamp())
    return None


def main_needs_extending(bundle, expire: str, now: int) -> bool:
    """A main key with an expiry that has passed, or that comes before the subkeys this run writes."""
    if bundle.expires <= 0:
        return False
    until = expire_at(expire, now)
    return bundle.expires <= now or until is None or bundle.expires < until
```

`mode_steps` gets `now: int = 0, unprotected: bool = False` (`unprotected` is used in Task 6). Prepend the main-key step for renew and rotate:

```python
def mode_steps(mode, bundle, targets, expire, algo, revoke_old, now=0, unprotected=False) -> list:
    fp = bundle.key_fp
    steps = []
    if mode in ("renew", "rotate") and main_needs_extending(bundle, expire, now):
        steps.append(Step("mode", STAGING, (*LOOPBACK, "--passphrase-fd", "0", "--quick-set-expire", fp, expire),
                          (PASS,)))
    if mode == "renew":
        fprs = unique(targets[u] for u in USAGES)
        steps.append(Step("mode", STAGING, (*LOOPBACK, "--passphrase-fd", "0", "--quick-set-expire", fp, expire,
                                            *fprs), (PASS,)))
        return steps
    if mode != "rotate":
        return steps
    steps += [Step("mode", STAGING, (*LOOPBACK, "--passphrase-fd", "0", "--quick-add-key", fp, ALGOS[algo][u],
                                     USAGE_WORD[u], expire), (PASS,)) for u in USAGES]
    if revoke_old:
        for old in unique(targets[u] for u in USAGES):
            steps.append(Step("mode", STAGING, ("gpgconf", "--kill", "all")))
            steps.append(Step("mode", STAGING, (*LOOPBACK, "--command-fd", "0", "--edit-key", fp),
                              (f"key {old}", "revkey", "y", "0", "", "y", PASS, "save")))
    return steps
```

`migrate.py`:
- `staging_steps` passes `int(time.time())` as `now`.
- In `validate_bundle`, before the subkey-expiry check (after the stubs check), add:

```python
        now = int(time.time())
        if mode == "clone" and 0 < self.bundle.expires <= now:
            raise RecoveryError("the main key has expired; use --mode renew or --mode rotate", EXIT_BACKUP)
```

Then reuse `now` in the existing `modes.expired(...)` call.

- [ ] **Step 4: Run the tests and the goldens**

Run: `cd recovery && python3 -m unittest tests.test_modes tests.test_backup tests.test_plan_parity.PythonPlanParityTests -v`
Expected: PASS, and `git diff --stat -- tests/golden` is empty (fixture main keys never expire).

- [ ] **Step 5: Commit**

```bash
git add recovery/ykrecover recovery/tests
git commit -m "recovery: extend an expired main key in renew and rotate"
```

---

### Task 5: Missing card slots (Python)

**Files:**
- Modify: `recovery/ykrecover/modes.py` (`select_targets`, `unique` users, `expired`, `default_algo`, `mode_steps`, `finish_steps`)
- Modify: `recovery/ykrecover/card.py` (`card_steps`, `verify_steps`)
- Modify: `recovery/ykrecover/migrate.py` (`validate_bundle`, `describe`, `dry_run_text`, `_check`)
- Test: `recovery/tests/test_modes.py`

**Interfaces:**
- `select_targets(bundle) -> dict[str, str | None]`: no longer raises.
- `SLOT_WORD = {"s": "sign", "e": "encrypt", "a": "authentication"}`.
- `missing(targets) -> list[str]`: usages whose target is `None`, in `s, e, a` order.
- `renew_targets(targets) -> dict`: `{u: targets[u] or ROTATED[u]}` (for dry-run plans).

**Behaviour:**
- **clone:**
  - Warnings `no <SLOT_WORD> subkey: that card slot stays empty`. For `a`, the warning adds `; SSH will not work from this card`.
  - If all three slots are missing, it raises `the key has no subkeys to put on a card; use --mode renew or --mode rotate` (`EXIT_BACKUP`).
- **renew:**
  - The subkey `--quick-set-expire` step is only added when there are existing targets.
  - It is followed by one `--quick-add-key <fp> <ALGOS[algo][u]> <USAGE_WORD[u]> <expire>` per missing usage, in `s, e, a` order.
- **Card steps:** skip the `keytocard` pair (kill plus edit-key) and the `set-touch` pair for a `None` slot.
- **Verify steps:**
  - without `s`: skip the sign and verify steps;
  - without `e`: skip encrypt, kill and decrypt;
  - `cardstatus` always stays.
- **`finish_steps`:** skips `--export-ssh-key` when `targets["a"]` is `None`.
- **The `cardstatus` check:** expects `""` for a `None` slot.

- [ ] **Step 1: Write failing tests (append to `tests/test_modes.py`)**

```python
from ykrecover import card

NOAUTH = (Subkey("S" * 40, "", "s", "ed25519", 1, 0, "u"), Subkey("E" * 40, "", "e", "cv25519", 1, 0, "u"))


class MissingSlotTests(unittest.TestCase):
    def test_select_targets_marks_missing(self):
        targets = modes.select_targets(bundle(subkeys=NOAUTH))
        self.assertEqual(targets, {"s": "S" * 40, "e": "E" * 40, "a": None})
        self.assertEqual(modes.missing(targets), ["a"])

    def test_renew_adds_missing_subkey(self):
        targets = modes.select_targets(bundle(subkeys=NOAUTH))
        steps = modes.mode_steps("renew", bundle(subkeys=NOAUTH), targets, "2y", "ed25519", False, NOW)
        self.assertEqual(steps[0].argv[-5:], ("--quick-set-expire", "F" * 40, "2y", "S" * 40, "E" * 40))
        self.assertEqual(steps[1].argv[-5:], ("--quick-add-key", "F" * 40, "ed25519", "auth", "2y"))

    def test_renew_without_any_subkey_only_adds(self):
        targets = modes.select_targets(bundle(subkeys=()))
        steps = modes.mode_steps("renew", bundle(subkeys=()), targets, "2y", "ed25519", False, NOW)
        self.assertEqual([s.argv[s.argv.index("--quick-add-key") + 3] for s in steps], ["sign", "encrypt", "auth"])

    def test_card_steps_skip_empty_slot(self):
        targets = {"s": "S" * 40, "e": "E" * 40, "a": None}
        steps = card.card_steps(1, "F" * 40, targets, card.CardSettings())
        keytocard = [s for s in steps if "--edit-key" in s.argv]
        self.assertEqual([s.stdin[2] for s in keytocard], ["1", "2"])
        touch = [s.argv[s.argv.index("set-touch") + 1] for s in steps if "set-touch" in s.argv]
        self.assertEqual(touch, ["sig", "dec"])

    def test_finish_skips_ssh_without_auth(self):
        steps = modes.finish_steps("F" * 40, "F" * 16, {"s": "S" * 40, "e": "E" * 40, "a": None})
        self.assertFalse(any("--export-ssh-key" in s.argv for s in steps))

    def test_default_algo_falls_back_to_main_key(self):
        self.assertEqual(modes.default_algo(bundle(subkeys=(), algo="rsa2048"), modes.select_targets(
            bundle(subkeys=(), algo="rsa2048"))), "rsa4096")
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd recovery && python3 -m unittest tests.test_modes -v`
Expected: FAIL (`select_targets` raises "has no auth subkey").

- [ ] **Step 3: Implement**

`modes.py`:

```python
SLOT_WORD = {"s": "sign", "e": "encrypt", "a": "authentication"}


def select_targets(bundle) -> dict:
    """The newest non-revoked subkey for each usage; None when the key has none."""
    targets = {}
    for usage in USAGES:
        usable = [k for k in bundle.subkeys if usage in k.usage and k.validity != "r"]
        targets[usage] = max(usable, key=lambda k: k.created).fpr if usable else None
    return targets


def missing(targets) -> list:
    return [u for u in USAGES if targets[u] is None]


def present(targets) -> list:
    return unique(targets[u] for u in USAGES if targets[u] is not None)


def renew_targets(targets) -> dict:
    return {u: targets[u] or ROTATED[u] for u in USAGES}


def expired(bundle, targets, now: int) -> list:
    by_fpr = {k.fpr: k for k in bundle.subkeys}
    return [f for f in present(targets) if 0 < by_fpr[f].expires <= now]


def default_algo(bundle, targets) -> str:
    ref = targets["s"] or targets["e"] or targets["a"]
    algo = next((k.algo for k in bundle.subkeys if k.fpr == ref), bundle.algo)
    return "rsa4096" if algo.startswith("rsa") else "ed25519"
```

In `mode_steps`:
- In the renew branch, `fprs = present(targets)`. Append the subkey expiry step only `if fprs`, then add:

```python
        steps += [Step("mode", STAGING, (*LOOPBACK, "--passphrase-fd", "0", "--quick-add-key", fp, ALGOS[algo][u],
                                         USAGE_WORD[u], expire), (PASS,)) for u in missing(targets)]
```

- In the rotate revoke loop, use `present(targets)`.

In `finish_steps`, only append the ssh step `if targets['a']`.

`card.py`:
- In the `keytocard` loop, `continue` if `targets[usage] is None`.
- In the touch loop, iterate `for usage, _, ref in SLOTS` and skip when `targets[usage] is None`.
- `verify_steps` builds its list conditionally:

```python
def verify_steps(index: int, key_fp: str, targets: dict) -> list:
    phase, v = f"verify{index}", VERIFYHOME
    pin = ("gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase-fd", "0", "--yes")
    steps = [
        Step(phase, STAGING, ("gpg", "--batch", "--yes", "--armor", "--output", f"{v}/pub.asc", "--export", key_fp)),
        Step(phase, v, ("gpg", "--batch", "--import", f"{v}/pub.asc")),
        Step(phase, v, ("gpg", "--batch", "--with-colons", "--card-status"), check="cardstatus"),
    ]
    if targets["s"]:
        steps += [Step(phase, v, (*pin, "--local-user", f"{targets['s']}!", "--output", f"{v}/test.sig",
                                  "--detach-sign", f"{v}/test.txt"), ("<USER_PIN>",)),
                  Step(phase, v, ("gpg", "--batch", "--verify", f"{v}/test.sig", f"{v}/test.txt"))]
    if targets["e"]:
        steps += [Step(phase, v, ("gpg", "--batch", "--yes", "--trust-model", "always", "--recipient",
                                  f"{targets['e']}!", "--output", f"{v}/test.gpg", "--encrypt", f"{v}/test.txt")),
                  Step(phase, v, ("gpgconf", "--kill", "all")),
                  Step(phase, v, (*pin, "--output", f"{v}/test.out", "--decrypt", f"{v}/test.gpg"), ("<USER_PIN>",),
                       check="roundtrip")]
    return steps
```

`migrate.py`:
- **`validate_bundle`:**
  - Use `modes.present(self.targets)` instead of `modes.unique(self.targets[u] for u in modes.USAGES)`.
  - After the expiry checks, add:

```python
        gaps = modes.missing(self.targets)
        if mode == "clone" and len(gaps) == len(modes.USAGES):
            raise RecoveryError("the key has no subkeys to put on a card; use --mode renew or --mode rotate",
                                EXIT_BACKUP)
        if mode == "clone":
            for u in gaps:
                extra = "; SSH will not work from this card" if u == "a" else ""
                self.ui.warn(f"no {modes.SLOT_WORD[u]} subkey: that card slot stays empty{extra}")
```

- **`describe()`:** after the subkey loop, add one line per missing slot (Ruling 2):

```python
        for u in modes.missing(self.targets):
            lines.append(f"  [{u.upper()}] none (clone leaves it empty; renew and rotate create one)")
```

- **`dry_run_text`:** use `targets = modes.ROTATED if mode == "rotate" else modes.renew_targets(self.targets) if mode == "renew" else self.targets`.
- **`_check` cardstatus:** use `want = [(self.targets[u] or "").upper() for u in modes.USAGES]`.

- [ ] **Step 4: Run the tests, goldens and suite**

Run: `cd recovery && python3 -m unittest tests.test_modes -v && DISPLAY=:0 YKRECOVER_REQUIRE_GUI=1 python3 -m unittest discover -s tests -t . && git diff --stat -- tests/golden`
Expected: PASS, with no golden diff. The bash parity tests still pass, because the existing cases are unchanged.

- [ ] **Step 5: Commit**

```bash
git add recovery/ykrecover recovery/tests
git commit -m "recovery: handle keys with an empty card slot"
```

---

### Task 6: Unprotected keys (Python)

**Files:**
- Modify: `recovery/ykrecover/card.py` (`card_steps` gains `unprotected=False`)
- Modify: `recovery/ykrecover/modes.py` (revoke stdin)
- Modify: `recovery/ykrecover/migrate.py` (`Session.unprotected`, `card_plan`, `staging_steps`, `run`, `_execute`)
- Test: `recovery/tests/test_modes.py`

**Interfaces:**
- `card.card_steps(index, key_fp, targets, settings, unprotected: bool = False)`. When unprotected, the `keytocard` stdin is `(f"key {fpr}", "keytocard", slot, "<ADMIN_PIN>", "save")`.
- In `modes.mode_steps(..., unprotected)`, the revoke stdin drops the `PASS` entry.
- `Session.unprotected` is a property: `bool(self.candidate and self.candidate.unprotected)`.

- [ ] **Step 1: Write failing tests**

```python
class UnprotectedTests(unittest.TestCase):
    def test_keytocard_without_passphrase_line(self):
        targets = {"s": "S" * 40, "e": "E" * 40, "a": "A" * 40}
        steps = card.card_steps(1, "F" * 40, targets, card.CardSettings(), unprotected=True)
        keytocard = [s for s in steps if "--edit-key" in s.argv]
        self.assertEqual(keytocard[0].stdin, ("key " + "S" * 40, "keytocard", "1", "<ADMIN_PIN>", "save"))

    def test_revoke_without_passphrase_line(self):
        targets = {"s": "S" * 40, "e": "E" * 40, "a": "A" * 40}
        steps = modes.mode_steps("rotate", bundle(), targets, "2y", "ed25519", True, NOW, unprotected=True)
        edits = [s for s in steps if "--edit-key" in s.argv]
        self.assertTrue(edits and all(modes.PASS not in s.stdin for s in edits))
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd recovery && python3 -m unittest tests.test_modes.UnprotectedTests -v`
Expected: FAIL (unexpected keyword `unprotected`).

- [ ] **Step 3: Implement**

`card.py`, in the `keytocard` loop:

```python
        secret = () if unprotected else ("<CERTIFY_PASS>",)
        steps += [kill, Step(phase, CARDHOME, (*CARD_GPG, "--edit-key", key_fp),
                             (f"key {targets[usage]}", "keytocard", slot, *secret, "<ADMIN_PIN>", "save"))]
```

`modes.py`, in the revoke loop:

```python
            secret = () if unprotected else (PASS,)
            steps.append(Step("mode", STAGING, (*LOOPBACK, "--command-fd", "0", "--edit-key", fp),
                              (f"key {old}", "revkey", "y", "0", "", "y", *secret, "save")))
```

`migrate.py`:
- Add the `unprotected` property.
- `staging_steps` passes `unprotected=self.unprotected`.
- `card_plan` passes `unprotected=self.unprotected` to `card.card_steps`.
- In `run()`, replace the passphrase loop with:

```python
        ui.info(session.describe())
        if session.unprotected:
            ui.warn("the backup's secret keys have no passphrase")
            if not session.unlock(""):
                raise RecoveryError("cannot use the backup's secret keys", EXIT_BACKUP)
        else:
            for _ in range(3):
                if session.unlock(ui.ask_secret("Certify key passphrase:")):
                    break
                ui.warn("wrong passphrase")
            else:
                raise RecoveryError("wrong passphrase three times", EXIT_BACKUP)
```

- In `_execute`, right after `self.apply_mode()`, add:

```python
        if o.mode != "clone" and self.unprotected:
            self.ui.warn(f"the new backup in {o.out} will also have no passphrase")
```

- [ ] **Step 4: Run the tests**

Run: `cd recovery && python3 -m unittest tests.test_modes -v && python3 -m unittest tests.test_plan_parity.PythonPlanParityTests && git diff --stat -- tests/golden`
Expected: PASS, with no golden diff.

- [ ] **Step 5: A live check on the fake hardware**

Read `recovery/tests/test_fake_hardware.py`. Add a Python-only test that runs a clone of `tests/fixtures/unprotected` against the fake card, the same way the existing clone test does. Assert that it succeeds, that stdin carries no passphrase prompt answer, and that stderr contains `the backup's secret keys have no passphrase`. If the fake ykman/gpg harness cannot express this, write down why in the commit message and skip this step. Do not weaken the harness.

- [ ] **Step 6: Commit**

```bash
git add recovery/ykrecover recovery/tests
git commit -m "recovery: use unprotected backups without a passphrase prompt"
```

---

### Task 7: New golden plans (Python)

**Files:**
- Modify: `recovery/tests/test_plan_parity.py`
- Create (generated): `recovery/tests/golden/{renew-expired-main-asc,rotate-expired-main,clone-no-auth,renew-no-auth,renew-split-files,clone-unprotected,clone-gnupg1-home}.plan`

- [ ] **Step 1: Add the cases to `CASES`**

```python
    "renew-expired-main-asc": ["--backup", "tests/fixtures/flexible/asc backup", "--mode", "renew"],
    "rotate-expired-main": ["--backup", "tests/fixtures/flexible/asc backup", "--mode", "rotate"],
    "clone-no-auth": ["--backup", "tests/fixtures/noauth/export"],
    "renew-no-auth": ["--backup", "tests/fixtures/noauth/export", "--mode", "renew"],
    "renew-split-files": ["--backup", "tests/fixtures/split", "--mode", "renew"],
    "clone-unprotected": ["--backup", "tests/fixtures/unprotected"],
    "clone-gnupg1-home": ["--backup", "tests/fixtures/gnupg1"],
```

- [ ] **Step 2: Generate them with Python**

Run: `cd recovery && UPDATE_GOLDEN=1 python3 -m unittest tests.test_plan_parity.PythonPlanParityTests -v`
Expected: PASS, and the seven new `.plan` files exist. `git diff --stat -- tests/golden` shows only new files.

- [ ] **Step 3: Read each new golden and check it by eye against the spec**

- `renew-expired-main-asc`:
  - STEP 1 is `--quick-set-expire <FP> 2y` (main key);
  - STEP 2 is `--quick-set-expire <FP> 2y <S> <E>`;
  - STEP 3 is `--quick-add-key <FP> rsa4096 auth 2y`;
  - then the 3 backup steps;
  - the card steps include slot 3 with `<NEW_FPR_A>`.
- `clone-no-auth`: no `keytocard 3`, no `set-touch aut`, no `--export-ssh-key`.
- `clone-unprotected`: the `keytocard` STDIN has no `<CERTIFY_PASS>` line.
- `clone-gnupg1-home` and `renew-split-files`: ordinary plans for their keys.

If anything differs, fix the code (not the golden) and regenerate.

- [ ] **Step 4: Commit**

```bash
git add recovery/tests/test_plan_parity.py recovery/tests/golden
git commit -m "recovery: golden plans for non-standard backups"
```

Bash parity tests now FAIL for the new cases. That is expected until Task 9. Do not push between Task 7 and Task 9.

---

### Task 8: Bash inventory, selection and report

**Files:**
- Modify: `recovery/recoverCard.sh` (replace `detect_dir`/`detect`; update `add_candidate`, `choose_candidate`, `load_candidate`, `main`)
- Create: `recovery/tests/test_inventory_parity.py`

**Interfaces:**
- Arrays: `CAND_KIND`, `CAND_PATH`, `CAND_FILES` (newline-joined), `CAND_KEY`, `CAND_UNPROT` (0/1).
- `INV_REPORT`: the report text, exactly as `backup.report()` produces it.
- `INV_FILES_FOUND`: count of key files.

- [ ] **Step 1: Write the failing parity test**

```python
"""The backup report (stderr) is identical in both implementations."""

import unittest

from tests.helpers import LINUX, run_impl

FOLDERS = ["tests/fixtures/flexible/asc backup", "tests/fixtures/ed25519", "tests/fixtures/ed25519/export",
           "tests/fixtures/split", "tests/fixtures/unprotected", "tests/fixtures/gnupg1", "tests/fixtures/multikey"]


def report(stderr: str) -> str:
    lines, inside = [], False
    for line in stderr.splitlines():
        if line.startswith("Backup files in "):
            inside = True
        elif inside and not line.startswith("  "):
            break
        if inside:
            lines.append(line)
    return "\n".join(lines)


@unittest.skipUnless(LINUX, "the bash implementation is Linux-only")
class InventoryParityTests(unittest.TestCase):
    def test_reports_match(self):
        for folder in FOLDERS:
            with self.subTest(folder):
                py = run_impl("python", ["--backup", folder, "--dry-run"])
                sh = run_impl("bash", ["--backup", folder, "--dry-run"])
                self.assertTrue(report(py.stderr), py.stderr)
                self.assertEqual(report(sh.stderr), report(py.stderr))
                self.assertEqual(sh.returncode, py.returncode, sh.stderr)
```

Run: `cd recovery && python3 -m unittest tests.test_inventory_parity -v`
Expected: FAIL (bash prints no report).

- [ ] **Step 2: Implement the walk, sniffing and classification in bash**

Add these functions in the "backup detection and loading" section, replacing `detect_dir` and `detect`:

```bash
looks_like_key_file() { # looks_like_key_file PATH: an armored key block or a binary key packet, ≤ 1 MiB
  local f=$1 head byte tag
  [[ -f $f && ! -L $f ]] || return 1
  (( $(stat -c %s -- "$f") <= 1048576 )) || return 1
  head=$(head -c 64 -- "$f" | tr -d '\000')
  head=${head#$'\xef\xbb\xbf'}
  head=${head#"${head%%[![:space:]]*}"}
  if [[ $head == '-----BEGIN PGP PRIVATE KEY BLOCK-----'* || $head == '-----BEGIN PGP PUBLIC KEY BLOCK-----'* ]]; then
    return 0
  fi
  byte=$(od -An -tu1 -N1 -- "$f" | tr -d ' \n')
  [[ -n $byte ]] && (( byte & 0x80 )) || return 1
  if (( byte & 0x40 )); then tag=$((byte & 0x3F)); else tag=$(((byte >> 2) & 0x0F)); fi
  [[ $tag == 5 || $tag == 6 || $tag == 7 || $tag == 14 ]]
}

is_gnupg2_home() { [[ -d $1/private-keys-v1.d ]] && { [[ -f $1/pubring.kbx ]] || [[ -f $1/pubring.gpg ]]; }; }

walk_dir() { # walk_dir DIR DEPTH: fills HOMES and KEY_FILES in byte order
  local dir=$1 depth=$2 entry name home=0 f files=()
  local LC_COLLATE=C
  if is_gnupg2_home "$dir"; then
    home=1
    for f in private-keys-v1.d pubring.kbx pubring.gpg trustdb.gpg; do
      if [[ -e $dir/$f ]]; then files+=("$dir/$f"); fi
    done
    add_candidate gnupghome "$dir" "" 0 "${files[@]}"
  fi
  for entry in "$dir"/*; do
    [[ -e $entry || -L $entry ]] || continue
    name=${entry##*/}
    [[ -L $entry ]] && continue
    if [[ -d $entry ]]; then
      if [[ $name != private-keys-v1.d ]] && (( depth < 3 )); then walk_dir "$entry" $((depth + 1)); fi
    elif (( home )) && [[ $name == pubring.kbx || $name == pubring.gpg || $name == trustdb.gpg ]]; then
      continue
    elif looks_like_key_file "$entry"; then
      KEY_FILES+=("$entry")
    fi
  done
}

classify_file() { # classify_file PATH → lines "MAINKID KEYID ROLE STATE"; non-zero when unreadable
  local out
  out=$(GNUPGHOME=$WORK gpg --batch --list-packets -- "$1" 2>/dev/null) || return 1
  awk '
    function close_part() {
      if (inpkt && kid != "") { if (state == "") state = "missing"; print main, kid, role, state }
      inpkt = 0; kid = ""
    }
    /^:/ {
      close_part()
      if ($0 ~ /^:(secret|public)( sub)? key packet:/) {
        inpkt = 1; role = ($0 ~ / sub key packet:/) ? "sub" : "main"
        state = ($0 ~ /^:public/) ? "public" : ""
      }
      next
    }
    !inpkt { next }
    /gnu-divert-to-card/ { state = "card-stub" }
    state == "" && /gnu-dummy/ { state = "missing" }
    state == "" && (/S2K/ || /^[ \t]*skey\[[0-9]+\]: .*protected\]$/) { state = "secret" }
    state == "" && /^[ \t]*skey\[[0-9]+\]: .*bits\]$/ { state = "secret-unprotected" }
    /^[ \t]*keyid:/ { kid = $2; if (role == "main") main = kid }
    END { close_part() }' <<<"$out" | grep . || return 1
}
```

**Parity note:** in Python, `gnu-divert-to-card` overrides any earlier state and other markers only fill an empty state. The awk above does the same. The `main` value carried into subkey parts is the last main keyid seen, as in Python.

- [ ] **Step 3: Implement selection and the report**

```bash
rank() { case $1 in secret) printf 2 ;; secret-unprotected) printf 1 ;; *) printf 0 ;; esac; }

state_word() {
  case $1 in secret) printf secret ;; secret-unprotected) printf 'secret (no passphrase)' ;;
    missing) printf missing ;; card-stub) printf 'card stub' ;; public) printf public ;; esac
}

describe_parts() { # describe_parts "ROLE STATE"... → "main secret, 2 subkeys secret"
  local line role state n s out="" sep=""
  for line in "$@"; do
    read -r role state <<<"$line"
    if [[ $role == main ]]; then out+="${sep}main $(state_word "$state")"; sep=", "; fi
  done
  for s in secret secret-unprotected missing card-stub public; do
    n=0
    for line in "$@"; do
      read -r role state <<<"$line"
      if [[ $role == sub && $state == "$s" ]]; then n=$((n + 1)); fi
    done
    if (( n == 1 )); then out+="${sep}1 subkey $(state_word "$s")"; sep=", "
    elif (( n > 1 )); then out+="${sep}$n subkeys $(state_word "$s")"; sep=", "; fi
  done
  printf '%s' "$out"
}

detect() { # walks BACKUP, classifies key files, fills CAND_* and INV_REPORT
  local root=${BACKUP%/} i j f key kid line main role state best order=() unreadable=()
  local -A parts_of=() seen_key=() status=() supplied=()
  [[ -d $root ]] || die "$EXIT_BACKUP" "backup folder not found: $BACKUP"
  KEY_FILES=()
  walk_dir "$root" 0
  INV_FILES_FOUND=${#KEY_FILES[@]}
  for i in "${!KEY_FILES[@]}"; do
    if ! line=$(classify_file "${KEY_FILES[i]}"); then unreadable+=("$i"); continue; fi
    while read -r main kid role state; do
      parts_of[$i,$main]+="$role $state $kid"$'\n'
      if [[ -z ${seen_key[$main]+set} ]]; then seen_key[$main]=1; order+=("$main"); fi
    done <<<"$line"
  done
  INV_UNRECOVERABLE=()
  for key in "${order[@]}"; do
    select_files "$key"
  done
  build_report "$root"
  sort_candidates
}
```

`select_files KEY` mirrors the Python `_select`. It must produce the same file order and the same `status[i,key]`. Implement it with this algorithm:
1. Collect the indexes `i` that have `parts_of[i,KEY]`.
2. Compute a sort key per index: `secret_count`, `main_secret` and `i`. Sort with `sort -k1,1nr -k2,2nr -k3,3n` over lines `"<secret_count> <main_secret> <i>"`.
3. Walk the sorted indexes. For each part line `role state kid`, if `$(rank state) > ${supplied[$kid]:-0}`, the file is used: set `supplied[$kid]` for each such part and add `i` to `used`.
4. Set `status[$i,$key]` to `used`, `not needed` (any part with rank > 0) or `ignored`.
5. If nothing was used, append the key to `INV_UNRECOVERABLE`. Otherwise:
   - kind is `export` if `supplied[$key]` > 0, else `subkeys-only`;
   - unprotected = 1 if every supplied value is 1;
   - path = `dirname` of the first used file;
   - call `add_candidate "$kind" "$path" "$key" "$unprot" "${used_paths[@]}"`.

`build_report ROOT` writes `INV_REPORT` exactly like Python's `report()`:
- the header `Backup files in <root>:` (`root` is `BACKUP` with a trailing `/` removed, the same string Python prints for `Path(BACKUP)`);
- then for each `KEY_FILES` index in order: if the index is unreadable, `  <rel>: not a readable key file (ignored)`; otherwise, for each key in `order` with `parts_of[i,key]`, `  <rel>: key <key>: $(describe_parts …) (<status>)`;
- then one `  key <k>: not recoverable from this folder (card stubs / public key only)` line per unrecoverable key.

`rel` is `${f#"$root"/}`. If there are no key files, `INV_REPORT=""`.

`sort_candidates` reorders the five `CAND_*` arrays by `(path bytes, kind order gnupghome=0, export=1, subkeys-only=2)` using `LC_ALL=C sort -t $'\t' -k1,1 -k2,2n` over lines `"<path>\t<order>\t<index>"`.

`add_candidate` becomes `add_candidate KIND DIR KEY UNPROT FILE...`. Set `CAND_KEY+=("$3")` and `CAND_UNPROT+=("$4")`, then `shift 4`.

- [ ] **Step 4: Update `choose_candidate`, loading and `main`**

```bash
choose_candidate() {
  local i matches=() listing="" want=${KEY^^} k
  want=${want// /}
  for i in "${!CAND_KIND[@]}"; do
    [[ -z $SOURCE || ${CAND_KIND[i]} == "$SOURCE" ]] || continue
    k=${CAND_KEY[i]}
    if [[ -n $want && -n $k && $want != *"$k" && $k != *"$want" ]]; then continue; fi
    matches+=("$i")
  done
  if (( ${#matches[@]} == 1 )); then CHOSEN=${matches[0]}; return 0; fi
  (( ${#matches[@]} )) ||
    die "$EXIT_BACKUP" "no backup found (expected secret-*-Certify.key, secret-*-Subkeys.key or a GnuPG home)"
  for i in "${matches[@]}"; do
    listing+=$'\n'"  ${CAND_KIND[i]}: ${CAND_PATH[i]}"
    if [[ -n ${CAND_KEY[i]} ]]; then listing+=" (key ${CAND_KEY[i]})"; fi
  done
  die "$EXIT_BACKUP" "several backups found; narrow --backup or pass --source or --key:$listing"
}
```

In `main()`, between `detect` and `choose_candidate`:

```bash
  if [[ -n $INV_REPORT ]]; then info "$INV_REPORT"; fi
  if (( ${#CAND_KIND[@]} == 0 )); then
    if (( INV_FILES_FOUND )); then
      die "$EXIT_BACKUP" "no secret keys in ${BACKUP%/}: only card stubs or public keys"
    fi
    die "$EXIT_BACKUP" "no backup found in ${BACKUP%/} (looked for OpenPGP key files and GnuPG homes)"
  fi
```

**Ordering:** Python prints the report in `run()`'s `finally` before the error is printed, so the stderr order is the same.

In `load_bundle`, when `--key` is empty, use `${CAND_KEY[CHOSEN]}` as the key filter: `want=${KEY^^}; [[ -n $want ]] || want=${CAND_KEY[CHOSEN]}`. A suffix match against the fingerprint works for a 16-hex keyid.

`load_candidate` imports `CAND_FILES[i]` in order. This is unchanged, since the stored order is already the use order.

- [ ] **Step 5: Run the tests**

Run: `cd recovery && python3 -m unittest tests.test_inventory_parity tests.test_cli_contract -v && shellcheck --severity=error recoverCard.sh`
Expected: PASS. Remove any Task 3 `expectedFailure` marker for bash. The existing bash plan goldens still pass; the new ones still fail until Task 9.

- [ ] **Step 6: Commit**

```bash
git add recovery/recoverCard.sh recovery/tests
git commit -m "recovery: choose backup files by content in the bash twin"
```

---

### Task 9: Bash expiry, missing slots and unprotected keys; contract tests

**Files:**
- Modify: `recovery/recoverCard.sh` (`list_keys`, `load_bundle`, `select_targets`, `unique_targets`, `check_bundle`, `rotate_algo`, `mode_steps`, `card_steps`, `verify_steps`, `finish_steps`, `dry_run`, `run_check`, describe, run_live passphrase handling)
- Modify: `recovery/tests/test_cli_contract.py`

**Interfaces:**
- New globals: `KEY_EXPIRES`, `KEY_ALGO`, `UNPROTECTED` (from `CAND_UNPROT[CHOSEN]`).
- `TARGET[u]` is `""` for a missing slot.

- [ ] **Step 1: Add the contract tests (both twins) to `test_cli_contract.py`**

Read the file first and follow its existing two-class pattern (`PythonContractTests` and `BashContractTests` sharing a mixin). Add these to the mixin:

```python
    def test_clone_with_expired_main_key(self):
        result = self.run_cli(["--backup", "tests/fixtures/flexible/asc backup", "--dry-run"])
        self.assertEqual(result.returncode, EXIT_BACKUP)
        self.assertIn("the main key has expired; use --mode renew or --mode rotate", result.stderr)

    def test_clone_without_auth_warns(self):
        result = self.run_cli(["--backup", "tests/fixtures/noauth/export", "--dry-run"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("warning: no authentication subkey: that card slot stays empty; SSH will not work from this card",
                      result.stderr)

    def test_only_stubs_is_not_recoverable(self):
        folder = short_tempdir(self, "stubs")
        stub = FIXTURES / "flexible" / "asc backup" / "stub" / "flex.priv_stub.asc"
        (folder / "stub.asc").write_bytes(stub.read_bytes())
        result = self.run_cli(["--backup", str(folder), "--dry-run"])
        self.assertEqual(result.returncode, EXIT_BACKUP)
        self.assertIn(f"no secret keys in {folder}: only card stubs or public keys", result.stderr)

    def test_empty_folder(self):
        folder = short_tempdir(self, "empty")
        result = self.run_cli(["--backup", str(folder), "--dry-run"])
        self.assertIn(f"no backup found in {folder} (looked for OpenPGP key files and GnuPG homes)", result.stderr)

    def test_several_keys_need_key(self):
        result = self.run_cli(["--backup", "tests/fixtures/multikey", "--dry-run"])
        self.assertEqual(result.returncode, EXIT_BACKUP)
        self.assertIn("several backups found; narrow --backup or pass --source or --key:", result.stderr)
        info = fixture_info("multikey")
        ok = self.run_cli(["--backup", "tests/fixtures/multikey", "--key", info["KEY2_FP"], "--dry-run"])
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn(info["KEY2_FP"], ok.stdout)
```

`run_cli` stands for whatever helper the file already uses. Adapt the names; don't add a second helper.

Run: `cd recovery && python3 -m unittest tests.test_cli_contract -v`
Expected: the Python twin PASSES; the bash twin FAILS on expiry, auth and multikey.

- [ ] **Step 2: Implement in bash, mirroring Tasks 4–6 exactly**

**`list_keys`:** the `sec` output line becomes `sec FPR KEYID SECRET EXPIRES ALGO`:

```awk
      $1 == "sec" { insec = 1; insub = 0; kid = $5; secret = ($15 == "#") ? 0 : 1
                    kexp = $7 + 0; kalgo = ($17 != "") ? $17 : "rsa" $3; next }
      $1 == "fpr" && insec { print "sec", $10, kid, secret, kexp, kalgo; insec = 0; next }
```

`load_bundle` reads `sec) current=$a; if [[ $a == "$KEY_FP" ]]; then KEY_ID=$b; HAS_SECRET=$c; KEY_EXPIRES=$d; KEY_ALGO=$e; fi`.

```bash
expire_at() { # expire_at EXPIRE NOW → epoch, or nothing for never / an unknown form
  local t=${1,,} n unit
  if [[ $t =~ ^([0-9]+)([dwmy]?)$ ]] && (( 10#${BASH_REMATCH[1]} > 0 )); then
    n=$((10#${BASH_REMATCH[1]}))
    case ${BASH_REMATCH[2]} in '' | d) unit=86400 ;; w) unit=604800 ;; m) unit=2592000 ;; y) unit=31536000 ;; esac
    printf '%s' $(($2 + n * unit))
  elif [[ $t =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    date -u -d "$t" +%s 2>/dev/null || true
  fi
}

main_needs_extending() { # main_needs_extending NOW
  local until
  (( KEY_EXPIRES > 0 )) || return 1
  until=$(expire_at "$EXPIRE" "$1")
  (( KEY_EXPIRES <= $1 )) || [[ -z $until ]] || (( KEY_EXPIRES < until ))
}
```

**Parity check for `expire_at`:** `date -u -d 2023-02-30` fails, so the result is empty, the same as Python's `ValueError` returning `None`.

**`select_targets`:** set `TARGET[$u]=$best`, which may be empty, and do not die.

**`unique_targets`:** skip empty targets.

**New `missing_slots` helper:** prints the usages with an empty target, in `s e a` order.

**`check_bundle`** follows Task 4 and Task 5 in this order:
1. the Certify check;
2. the stubs check (over non-empty targets);
3. `clone` with `0 < KEY_EXPIRES <= now` → die with `the main key has expired; use --mode renew or --mode rotate`;
4. the subkey expiry check;
5. `clone` with all three slots missing → die with `the key has no subkeys to put on a card; use --mode renew or --mode rotate`;
6. `clone` warnings, one per missing slot: `no <sign|encrypt|authentication> subkey: that card slot stays empty`, plus `; SSH will not work from this card` for `a`.

**`rotate_algo`:** use the sign target, else encrypt, else auth, else `KEY_ALGO`. Map `rsa*` to `rsa4096` and anything else to `ed25519`. Rename it `default_algo` and use it for renew as well.

**`mode_steps`:**
- renew and rotate first call `if main_needs_extending "$(date +%s)"; then STEP_STDIN=('<CERTIFY_PASS>'); step mode '<STAGING>' '' "${LOOPBACK[@]}" --passphrase-fd 0 --quick-set-expire "$KEY_FP" "$EXPIRE" || return 1; fi`;
- renew: the subkey step only if `fprs` is non-empty, then `--quick-add-key "$KEY_FP" "$(algo_for "$algo" "$u")" "$(usage_word "$u")" "$EXPIRE"` for each missing usage;
- the revoke stdin drops `'<CERTIFY_PASS>'` when `UNPROTECTED == 1`.

**`card_steps`:**
- skip the `keytocard` kill and edit pair and the `set-touch` kill and step pair for an empty `TARGET`;
- the `keytocard` stdin is `("key …" keytocard "$slot" '<ADMIN_PIN>' save)` when `UNPROTECTED == 1`.

**`verify_steps`:** skip sign and verify when `TARGET[s]` is empty, and encrypt, kill and decrypt when `TARGET[e]` is empty.

**`finish_steps`:** skip the ssh export when `TARGET[a]` is empty.

**`dry_run`:** for `renew`, replace empty targets with `<NEW_FPR_S|E|A>` before the card steps. rotate is unchanged.

**`run_check cardstatus`:** compares against the possibly empty targets. `${TARGET[s]^^}` of an empty string is empty, which matches an empty card slot.

**Describe output and live passphrase:**
- The describe output (live run only) adds `  [A] none (clone leaves it empty; renew and rotate create one)` lines.
- The live run with `UNPROTECTED == 1` warns `the backup's secret keys have no passphrase` and uses `CERTIFY_PASS=""` without prompting.
- After the mode is applied for renew or rotate, it warns `the new backup in $OUT will also have no passphrase`.

Set `UNPROTECTED=${CAND_UNPROT[CHOSEN]}` in `main()` after `choose_candidate`.

- [ ] **Step 3: Run the full parity and contract suite**

Run: `cd recovery && python3 -m unittest tests.test_plan_parity tests.test_cli_contract tests.test_inventory_parity -v && shellcheck --severity=error recoverCard.sh`
Expected: PASS for both twins, including every new golden.

- [ ] **Step 4: Run the whole suite**

Run: `cd recovery && DISPLAY=:0 YKRECOVER_REQUIRE_GUI=1 python3 -m unittest discover -s tests -t .`
Expected: PASS. `git diff --stat master -- recovery/tests/golden` shows only the seven new files.

- [ ] **Step 5: Commit**

```bash
git add recovery/recoverCard.sh recovery/tests
git commit -m "recovery: main-key renewal, empty slots and unprotected keys in the bash twin"
```

---

### Task 10: GUI

**Files:**
- Modify: `recovery/ykrecover/gui.py` (`build_backup` area around the candidate list, `refresh_candidates`, `leave_backup`, `gui_wording` table)
- Test: `recovery/tests/test_gui.py`

**Interfaces:**
- Consumes: `backup.inventory`, `backup.report`, `Candidate.key_id` and `Candidate.unprotected`.
- `App.inventory`, `App.report_box` (the read-only text area) and `App.pass_entry` (the passphrase `ttk.Entry`).

- [ ] **Step 1: Write failing tests**

Add them to `test_gui.py`, following the existing `GuiBehaviourTests` and `GuiLayoutTests` fixtures. Read them first: `self.app()`, `SCALINGS`, `fake_session` and `when_dialog_up`.

```python
    def test_backup_page_shows_the_report(self):
        app = self.app()
        app.show(1)
        app.v["folder"].set(str(FIXTURES / "flexible" / "asc backup"))
        app.refresh_candidates()
        text = app.report_box.get("1.0", "end")
        self.assertIn("private/flex.priv.asc", text)
        self.assertIn("(used)", text)
        self.assertIn("key " + fixture_info("flexible")["KEY_ID"], app.cand_list.get(0))

    def test_unprotected_candidate_disables_the_passphrase(self):
        app = self.app()
        app.show(1)
        app.v["folder"].set(str(FIXTURES / "unprotected"))
        app.refresh_candidates()
        app.cand_list.selection_set(0)
        app.cand_list.event_generate("<<ListboxSelect>>")
        app.root.update()
        self.assertIn("disabled", app.pass_entry.state())

    def test_key_candidates_load_from_the_root_folder(self):
        app = self.app()
        app.show(1)
        app.v["folder"].set(str(FIXTURES / "split"))
        app.refresh_candidates()
        app.cand_list.selection_set(0)
        app.v["passphrase"].set(PASSPHRASE)
        self.assertTrue(app.leave_backup())
        self.assertEqual(app.session.opts.backup, FIXTURES / "split")
        self.assertTrue(app.session.bundle.has_certify_secret)
```

In `GuiLayoutTests`, add a fit test with a long report:

```python
    def test_backup_page_fits_with_a_long_report(self):
        root, app = self.app(SCALINGS[2])
        width, height = root.minsize()
        root.geometry(f"{width}x{height}")
        app.show(1)
        app.set_report("\n".join(f"  folder/sub/file-{i:02d}.asc: key 0123456789ABCDEF: main secret, "
                                 f"3 subkeys secret (not needed)" for i in range(20)))
        root.update()
        self.assertEqual(root.minsize(), (width, height))
        self.assertLessEqual(root.winfo_reqheight(), height)
```

Run: `cd recovery && DISPLAY=:0 YKRECOVER_REQUIRE_GUI=1 python3 -m unittest tests.test_gui -v`
Expected: FAIL (`report_box` and `set_report` are missing).

- [ ] **Step 2: Implement**

- **Report area on the backup page:** under the candidate list (after `cand_error`), add a read-only text area made the same way as the other read-only text areas, `self.readonly_text("", 4)` or equivalent. Store it in `self.report_box`, and add `set_report(text)`, which replaces its contents. It must use the existing ScrolledText, `wrap="word"` and `width=1` rules.
- **`refresh_candidates`:**
  - Call `inv = backup_mod.inventory(Path(folder))`, `self.inventory = inv`, `self.candidates = list(inv.candidates)` and `self.set_report(backup_mod.report(inv))`.
  - Each list row is `f"{c.kind}: {c.path}" + (f"  key {c.key_id}" if c.key_id else "") + ("  (no passphrase)" if c.unprotected else "")`.
  - Errors go to `cand_error` as today, and the report is cleared then.
- **Passphrase field:** store the passphrase `ttk.Entry` in `self.pass_entry`. Bind `<<ListboxSelect>>` on `cand_list` to `self.update_pass_state`. That method disables the entry and clears the passphrase variable if the selected candidate is unprotected, and re-enables it otherwise. Also call it after `refresh_candidates` auto-selects a single candidate.
- **`leave_backup` (Ruling 4):**
  - Build the options as `Options(backup=candidate.path if candidate.kind == "gnupghome" else Path(self.v["folder"].get()), source=candidate.kind, key=candidate.key_id or None, workdir=workdir)`.
  - The "already loaded" shortcut compares `(s.opts.backup, s.opts.source, s.opts.key, s.opts.workdir)` with the new values.
  - For an unprotected candidate, call `self.session.unlock("")`. If it fails, alert "The backup's secret keys could not be used." and return False.
  - Check whether `Options` has a `key` field (CLI `--key` exists, so it should); add it with default `""` if not.
- **`gui_wording`:** add entries so that new errors naming CLI flags read naturally:
  - `"use --mode renew or --mode rotate"` becomes `"choose Renew or Rotate on the Mode page"`;
  - `"narrow --backup or pass --source or --key"` becomes `"choose a narrower folder"`.

- [ ] **Step 3: Run the GUI tests and the whole suite**

Run: `cd recovery && DISPLAY=:0 YKRECOVER_REQUIRE_GUI=1 python3 -m unittest discover -s tests -t .`
Expected: PASS. If the 150% fit test fails, reduce the report box to 3 lines rather than growing the window; the window size rule stays as it is.

- [ ] **Step 4: Commit**

```bash
git add recovery/ykrecover/gui.py recovery/tests/test_gui.py
git commit -m "recovery: show the backup report in the wizard"
```

---

### Task 11: Documentation

**Files:**
- Modify: `recovery/README.md`

- [ ] **Step 1: Add a "What counts as a backup" section**

Read `recovery/README.md` first and put the section next to the existing description of `--backup`. It lists:
- the guide's files (`secret-*-Certify.key`, `secret-*-Subkeys.key`);
- any-name armored or binary OpenPGP key files, up to 3 folder levels deep;
- one key split across files;
- several keys in one file (pick one with `--key`);
- unprotected exports (no passphrase prompt);
- GnuPG 2.x homes;
- GnuPG 1.x homes (`secring.gpg`).

It also states that card stubs, `.rev` files and public keys are reported but never used. Include a sample report, using the `flexible` fixture's layout with a made-up keyid `0123456789ABCDEF`.

- [ ] **Step 2: Document renew and rotate behaviour**

Add to the existing modes description:
- an expiring or expired main key is extended to `--expire` first, while a main key that never expires is untouched;
- renew and rotate create a missing sign, encrypt or authentication subkey;
- clone leaves an empty slot empty and warns, and writes no `ssh-*.pub` without an authentication subkey.

- [ ] **Step 3: Lint**

Run: `cd /home/mezinster/YubiKey-Guide && source ~/.nvm/nvm.sh && nvm use default >/dev/null && npx --yes markdownlint-cli2@0.23.3 && python3 .github/scripts/check_toc.py README.md`
Expected: 0 issues.

- [ ] **Step 4: Commit**

```bash
git add recovery/README.md
git commit -m "docs: describe the backup layouts ykrecover accepts"
```

---

## Final verification (after Task 11)

Run from `recovery/`:
- `DISPLAY=:0 YKRECOVER_REQUIRE_GUI=1 python3 -m unittest discover -s tests -t .`: all pass.
- `shellcheck --severity=error ../scripts/*.sh ../piv/*.sh recoverCard.sh tests/fixtures/make_fixtures.sh`: clean.
- `git diff --stat master -- tests/golden`: only the seven new `.plan` files.
- `python3 -m ykrecover --cli --backup "tests/fixtures/flexible/asc backup" --mode renew --dry-run`: prints the report on stderr and a plan that starts with the main-key `--quick-set-expire`.
