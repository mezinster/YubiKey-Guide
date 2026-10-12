# ykrecover: Flexible Backups and Main-Key Renewal — Design

Date: 2026-10-12
Status: approved in brainstorming, pending spec review
Builds on: `docs/superpowers/specs/2026-10-11-yubikey-recovery-design.md` (the toolset) and `docs/superpowers/specs/2026-10-11-ykrecover-windows-design.md` (Windows). Everything there still applies unless this document says otherwise.

## Purpose

Real backups often differ from the layout the guide produces. One example is a 2021 backup of key `FEDCBA9876543210FEDCBA980123456789ABCDEF`:

```text
backup/
  89ABCDEF.rev
  private/89abcdef.priv.asc         main key + 2 subkeys, all secret (passphrase-protected)
  subkeys/89abcdef.sub_priv.asc     main key gnu-dummy, 2 subkeys secret
  stub/89abcdef.priv_stub.asc       everything gnu-divert-to-card
  stub/89abcdef.sub_priv_stub.asc   main key gnu-dummy, subkeys gnu-divert-to-card
  pub/89abcdef.pub.asc              public key
```

Today both twins (`ykrecover` and `recoverCard.sh`) report "no backup found" for it, because they only look for `secret-*-Certify.key`, `secret-*-Subkeys.key` or a GnuPG home. If they did load it, they would still fail on two other points:
- **No authentication subkey:** the key has only `[SC]` main, `[E]` and `[S]`, and both twins require all three card slots.
- **Expired main key:** the main key expired on 2023-02-27. `--mode renew` only extends subkeys, so the key would stay unusable.

### Success criteria

- Pointed at a folder like the one above, both twins:
  1. print, for each key-bearing file, what it holds and whether it is used or ignored;
  2. load the best secret material;
  3. with `--mode renew`, extend the main key and the subkeys and create the missing authentication subkey;
  4. write the result to cards.

  No files are renamed by hand.
- Python and bash print byte-identical `--dry-run` plans for every new case. New golden plans cover each case.
- Backups in the guide's layout produce exactly today's plans and messages. Existing goldens and tests are unchanged.

### Decisions (from brainstorming)

- **Full bash parity:** `recoverCard.sh` gets every feature in this document.
- **Detection by content:**
  - `gpg --list-packets` classifies each file, read-only, with no agent and no import (approach 1).
  - The chosen files are then imported into staging.
  - File names and extensions are ignored.
- **Missing card slot:**
  - `clone` leaves the slot empty and warns.
  - `renew` and `rotate` create the missing subkey.
- **In scope:**
  - any-name armored or binary key files, in nested folders;
  - several keys in one file, and one key split across files;
  - unprotected secret keys;
  - GnuPG 1.x homes (`secring.gpg`).
- **Out of scope:**
  - a separate report-only `analyze` command;
  - encrypted or compressed archives (`.zip`, `.tar`, symmetric `.gpg`);
  - paperkey;
  - setting a new passphrase on an unprotected key.

## Components

### Inventory (`recovery/ykrecover/backup.py`; bash: `inventory` functions in `recoverCard.sh`)

#### Walk

- The walk covers the backup folder and its subfolders up to depth 3 (the folder itself is depth 0). It skips names starting with `.` and does not descend into `private-keys-v1.d`.
- Symlinks are not followed.
- Entries are visited in sorted order (byte order of the path) so both twins agree.

#### GnuPG homes

These rules are kept from today, and 1.x is new:
- **2.x home:** a folder containing `private-keys-v1.d/` and `pubring.kbx` or `pubring.gpg`. It is loaded by copying, as today.
- **1.x home:** a folder containing `secring.gpg` and no `private-keys-v1.d/`. It becomes a file entry for `secring.gpg`, classified and imported like any other key file. Its `pubring.gpg` is ignored, because `secring.gpg` already carries the public parts.

#### Key files

A key file is any regular file of 1 MiB or less whose first bytes are one of:
- `-----BEGIN PGP PRIVATE KEY BLOCK-----`, possibly after leading whitespace, a BOM, or CRLF line endings;
- `-----BEGIN PGP PUBLIC KEY BLOCK-----`;
- a binary OpenPGP packet header for a secret-key, public-key, secret-subkey or public-subkey packet: tag 5, 6, 7 or 14, in old or new format.

Other files are ignored silently. That includes `.rev` files, which are signature blocks.

#### Classification

- Each key file goes through `gpg --batch --list-packets <file>`, with `GNUPGHOME` set to a throwaway staging home.
- The output is parsed into parts, one per secret or public key packet. The fields are:
  - `key_fp`: fingerprint of the main key the part belongs to, the last main-key packet seen;
  - `keyid`;
  - `role`: `main` or `sub`;
  - `state`.
- `state` is decided from the lines that follow the packet header:

  | Lines after the packet header | state |
  | - | - |
  | `gnu-divert-to-card` | `card-stub` |
  | `gnu-dummy` | `missing` |
  | `S2K` line, or `skey[N]: [v4 protected]` | `secret` |
  | no protection line, `skey[N]: [<n> bits]` | `secret-unprotected` |
  | `:public key packet:` / `:public sub key packet:` | `public` |

- `--list-packets` prints key IDs, not fingerprints. Parts are therefore grouped by the main-key keyid.
- The full fingerprint and the subkey usages come later, from `--list-secret-keys --with-colons` after import, as today.
- If a file cannot be parsed (gpg exits non-zero, or no key packets), it is reported as `unreadable` and ignored.

#### Selection per key (grouped by main-key keyid)

- **A file is used** if it holds at least one part in state `secret` or `secret-unprotected` that no earlier-chosen file already supplies in a better state. The order of preference is `secret` > `secret-unprotected` > `missing` / `card-stub` / `public`.
- **Candidate order:** files with more secret parts come first, then by sorted path. Ties go to the file holding the main-key secret.
- **Recoverable** means at least one secret part. The key is:
  - *full* if the main key is secret in some used file;
  - *subkeys-only* otherwise.
- **Not recoverable** means only stubs, missing parts or public parts. Such a key is listed with "not recoverable from this folder (card stubs / public key only)".

#### Report

The report is printed by the CLI before the passphrase prompt and shown on the GUI backup page. It has one line per key file, with the path relative to the backup folder. Example:

```text
Backup files:
  private/89abcdef.priv.asc        key 89ABCDEF: main + 2 subkeys secret          used
  subkeys/89abcdef.sub_priv.asc    key 89ABCDEF: 2 subkeys secret, main missing   not needed
  stub/89abcdef.priv_stub.asc      key 89ABCDEF: card stubs only                  ignored
  stub/89abcdef.sub_priv_stub.asc  key 89ABCDEF: card stubs only                  ignored
  pub/89abcdef.pub.asc             key 89ABCDEF: public key only                  ignored
```

- Unprotected parts add `(no passphrase)` to the line.
- The exact wording is part of the plan output contract, so both twins print the same text.
- The report goes to **stderr**, for guide-layout backups too. The golden tests compare the whole of stdout, so `--dry-run` plans stay byte-identical. The report's exact text has its own parity test.

#### Candidates

- The existing `Candidate(kind, path, files)` stays.
- Kinds:
  - `gnupghome` is unchanged;
  - `export` covers any recoverable key with a main-key secret;
  - `subkeys-only` covers the rest.
- `files` lists the used files in import order.
- One candidate exists per recoverable key, or one per GnuPG home.
- `--source export|subkeys-only|gnupghome` keeps its meaning. `--key` picks the key when there are several, as today.
- Guide-layout folders produce the same candidates and the same files as today.

#### Loading

- `load()` imports the candidate's files in order with `gpg --batch --import`.
- Secret files are imported first, and stubs are never imported. A missing part therefore can never shadow a secret one.
- The existing `--list-secret-keys` parsing then applies.

### Main-key expiry (`recovery/ykrecover/modes.py`; bash: mode step builders)

- `KeyBundle` gains `expires: int`, read from field 7 of the `sec` line, where `0` means never.
- Let `T` be the new expiry the run will apply (from `--expire`).
- **The main key needs extending** when it has an expiry (`expires > 0`) and that expiry is in the past or comes before the expiry of the subkeys this run writes.
  - That comparison is made by evaluating `--expire` against now. `0`, `never` and `none` mean never; then any finite main-key expiry is extended.
- **renew:**
  - When the main key needs extending, a first step is added: `gpg --batch --pinentry-mode loopback --passphrase-fd 0 --quick-set-expire <fp> <expire>`, with no subkey list.
  - The existing subkey step follows.
- **rotate:** the same first step is added before `--quick-add-key`.
- **clone:** an expired main key stops with `the main key has expired; use --mode renew or --mode rotate` (`EXIT_BACKUP`), checked before the subkey expiry check.
- A main key with no expiry is never touched. Guide keys therefore keep today's plans.

### Missing card slots (`select_targets`, card and finish steps)

- `select_targets` returns `None` for a usage with no usable (non-revoked) subkey, instead of raising.
- **clone:**
  - Clone steps skip `keytocard` and verification for that slot.
  - If the slot is `a`, the finish step skips `--export-ssh-key`.
  - Warning: `no <sign|encrypt|authentication> subkey: that card slot stays empty` (for `a`, also `SSH will not work from this card`).
  - If every slot is missing, clone stops with `the key has no subkeys to put on a card; use --mode renew or --mode rotate`.
- **renew:**
  - For each missing usage, a step `--quick-add-key <fp> <algo> <sign|encrypt|auth> <expire>` is added after the expiry steps.
  - Its new fingerprint is a plan placeholder `<NEW_FPR_S|E|A>`, the same as in rotate. It is resolved after the step from the subkey list, as rotate already does.
- **rotate:** unchanged, because it already adds all three.
- **Algorithm for created subkeys:**
  - `--algo` if given;
  - else `default_algo`: `rsa*` gives `rsa4096` and anything else gives `ed25519`/`cv25519`;
  - judged from the sign target, else the encrypt target, else the main key's algorithm.
- **`describe()`** prints the missing slot as `  [A] none (will be created)` in renew/rotate and `  [A] none (slot left empty)` in clone.

### Unprotected keys

- The inventory marks a candidate *unprotected* when every secret part it supplies is `secret-unprotected`.
- **CLI:** the passphrase prompt is skipped and `CERTIFY_PASS` is empty. Warning: `the backup's secret keys have no passphrase`.
- **renew/rotate:** the summary adds `the new backup in <out> will also have no passphrase`.
- **GUI:** the passphrase field is disabled with that note.
- `check_passphrase` with the empty passphrase must still pass. This is verified before cards are touched, as today.
- **Mixed candidates:** a candidate that mixes protected and unprotected parts asks for the passphrase, as today.

### GUI (`recovery/ykrecover/gui.py`)

- The backup page shows the report from the inventory in the existing read-only text area, with the same wrap and scroll rules as the other text areas.
- The key chooser lists each recoverable key with its kind (full / subkeys-only).
- Existing layout tests cover the new text. A test feeds a 20-file report at 150% scaling and asserts that the page still fits.

## Error handling

| Situation | Message | Exit |
| - | - | - |
| No key files and no GnuPG home | `no backup found in <folder> (looked for OpenPGP key files and GnuPG homes)` | `EXIT_BACKUP` |
| Key files found, none recoverable | The report, then `no secret keys in <folder>: only card stubs or public keys` | `EXIT_BACKUP` |
| Main key expired, `clone` | `the main key has expired; use --mode renew or --mode rotate` | `EXIT_BACKUP` |
| No subkeys at all, `clone` | `the key has no subkeys to put on a card; use --mode renew or --mode rotate` | `EXIT_BACKUP` |
| Unreadable key-looking file | Reported as `unreadable` in the report; not fatal | — |

## Testing

Fixtures are added to `recovery/tests/fixtures/make_fixtures.sh` and generated with gpg 2.x:

- `flexible-asc`: the layout of the example above, with an `[SC]` main key, `[E]`, `[S]`, no `[A]`, and a main key and subkeys already expired.
  - The `missing` file comes from `--export-secret-subkeys`.
  - Card-stub files can't be produced without a card, so a small stdlib Python helper called by the fixture script rewrites a binary secret export. It replaces each key's secret part with a GNU divert-to-card S2K: usage 255, S2K type 101, `GNU` mode 2, serial length and serial. It then re-armors the result.
  - The parser test checks that gpg reports `gnu-divert-to-card` for the result.
- `split-files`: main key secret in one file, subkeys secret in another.
- `unprotected`: an export without a passphrase.
- `gnupg1-home`: a folder with `secring.gpg` (a binary `--export-secret-keys`, the same packet format GnuPG 1 used) and `pubring.gpg`.
- `multi-key`: one armored file with two keys.

Tests:
- **Unit tests** for the `--list-packets` parser, against the exact output lines gpg 2.4 prints. These include the five states and the unreadable case.
- **New golden plans, checked in both twins:**
  - `renew-expired-main-asc`: expiry on the main key, then the subkeys, then a new `[A]`;
  - `clone-no-auth`: no `[A]` slot steps and no `ssh-*.pub`;
  - `renew-split-files`;
  - `clone-unprotected`;
  - `clone-gnupg1-home`;
  - `rotate-expired-main`.
- **Inventory report tests** check the exact text in both twins.
- **Contract tests** for the new error messages in both twins.
- **GUI:** report display, the disabled passphrase field for unprotected keys, and the 150% layout fit.
- Existing goldens and tests stay unchanged.

## Documentation

`recovery/README.md` gets:
- a "What counts as a backup" section listing the accepted shapes;
- a sample of the report;
- the renew behaviour for the main key and for missing subkeys.
