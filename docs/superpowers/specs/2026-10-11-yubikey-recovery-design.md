# YubiKey Recovery Toolset — Design

Date: 2026-10-11
Status: approved in brainstorming, pending spec review

## Purpose

The owner's current YubiKeys are partly damaged. Offline backups of the GPG key material exist, created per this guide before `keytocard`. The toolset migrates those backups onto a new set of YubiKeys, with a Python GUI and an equally complete bash implementation.

### Success criteria

- Each new card passes verification: on-card fingerprints match the intended subkeys, sign/verify and encrypt/decrypt round-trip, and the card's SSH public key matches.
- The daily machine's keyring recognizes the new cards (stubs updated).
- The original backup is never modified.

### Scope

- In: Linux (this repo's NixOS LiveCD, Debian/other live systems, a regular workstation). One session handles 1..N cards, all receiving the same subkeys.
- Out: macOS/Windows, PIV, FIDO/OTP configuration, publishing keys to keyservers, paperkey restore.

### Assumptions

- Damaged cards are not lost or compromised. If they might be, the user should choose `rotate`; the tool states this on the mode page.

## Layout

```text
recovery/
  ykrecover/
    __init__.py
    __main__.py   # `python -m ykrecover` → GUI; `--cli` → terminal; `update-stubs` subcommand
    backup.py     # detect and load backups → KeyBundle
    gpg.py        # gpg / gpg-agent wrappers, ephemeral GNUPGHOME lifecycle, secret-safe I/O
    card.py       # ykman and gpg --card-edit operations
    modes.py      # clone / renew / rotate applied to staging
    plan.py       # build the canonical step list (used by execution and --dry-run)
    migrate.py    # per-card pipeline, verification, retry
    gui.py        # Tkinter wizard
    cli.py        # terminal front end (argparse + getpass)
  recoverCard.sh  # full bash implementation of the same contract
  tests/
  README.md       # usage, safety notes, manual hardware checklist
```

Constraints:

- Python ≥ 3.11, standard library only (Tkinter included). No pip installs at runtime, so the tool works air-gapped.
- Bash script follows `scripts/` conventions: `#!/usr/bin/env bash`, `set -o errtrace -o nounset -o pipefail -o errexit`, upstream-style header comment.
- External tools: `gpg` ≥ 2.2, `gpg-connect-agent`, `gpgconf`, `ykman`, `pcscd`/`scdaemon`.

The two implementations share a **contract**, not code: identical options, steps, step order, `--dry-run` plan output and exit codes. Parity is enforced by tests (see Testing).

### Nix integration

`nix/flake.nix` adds `python3` with Tkinter to the ISO, installs `recovery/` into the image, puts `recoverCard.sh` and a `yubikey-recovery` wrapper (`python3 -m ykrecover`) on `PATH`, and adds a desktop launcher next to the existing guide shortcut.

## Data model

`KeyBundle` (Python) / equivalent shell variables (bash):

- `source_kind`: `export` (`secret-*-Certify.key` [+ `secret-*-Subkeys.key`]), `subkeys-only` (only `*-Subkeys.key`), or `gnupghome` (`private-keys-v1.d/` + `pubring.kbx`)
- `key_id`, `key_fp`, `uids`
- `subkeys`: list of `{fpr, keygrip, usage (S/E/A), algo, expires}`
- `has_certify_secret`: bool — false for `subkeys-only`; `renew`/`rotate` require true

If a folder matches more than one format, the user picks. If multiple primary keys are found, the user picks one.

## Pipeline

### 1. Preflight

- Required tools present and `gpg` ≥ 2.2; otherwise exit 1.
- Every gpg call sets `GNUPGHOME` explicitly to a tool-owned home; an inherited `GNUPGHOME` is ignored. The tool never reads or writes `~/.gnupg` except in `update-stubs`.
- Warn (not fail) when any non-loopback network interface is up.
- `pcscd` reachable and at least one card reader visible before the card loop starts.

### 2. Load backup (read-only)

- User selects a folder (typically the mounted LUKS backup volume). Detect format per Data model.
- Create staging home under `$XDG_RUNTIME_DIR` (fallback: `mktemp -d` with a warning that it may not be tmpfs), mode 0700; write `config/gpg.conf` from this repo into it if available.
- `export`/`subkeys-only`: `gpg --import` the files. `gnupghome`: copy `private-keys-v1.d/`, `pubring.kbx`, `trustdb.gpg` into staging. Source files are only ever read.
- Verify the Certify passphrase by a loopback export to `/dev/null`. Wrong passphrase → re-prompt; three failures → exit 2.
- Show key ID, fingerprint, UIDs and each subkey (usage, algorithm, expiry) for confirmation. Expired subkeys are flagged and the user is offered `renew`.

### 3. Mode (applied once, in staging)

- `clone`: no change.
- `renew`: `gpg --quick-set-expire $KEY_FP $EXPIRE <subkey fprs>` (default `2y`).
- `rotate`: `gpg --quick-add-key $KEY_FP <algo> sign|encrypt|auth $EXPIRE` for each usage. Algorithms: `rsa4096` → rsa4096 for all three; `ed25519` → ed25519 sign/auth + cv25519 encrypt. Optional: revoke the previous subkeys (default: leave them to expire).
- For `renew` and `rotate`, the tool **requires** writing a new backup before any card is touched: `secret-$KEY_ID-Certify.key`, `secret-$KEY_ID-Subkeys.key`, `public-$KEY_ID-<date>.asc` into the chosen output folder. The output folder must differ from the backup folder. The user must confirm the new backup was saved; card writing stays disabled until then.
- The set of subkeys written to cards is the newest valid S, E and A subkey.

### 4. Card settings (collected once)

- PINs: `generate` (README method: 8-digit Admin, 6-digit User) or `enter`; `--same-pins` uses one pair for all cards, otherwise per card. Minimum lengths enforced (User ≥ 6, Admin ≥ 8).
- Retries: default `5,5,5` (`ykman openpgp access set-retries`).
- Key attributes: derived from the subkeys to write; not user-editable.
- Touch policy: `off|on|fixed|cached|cached-fixed` per slot, default `on` for sig/enc/aut.
- Cardholder: name (default blank), login (default random `yk.<16 chars>` per the README's non-identifying guidance), public key URL (default blank). The GUI repeats the README warning that card metadata is readable by anyone holding the card.

### 5. Per-card loop (N times)

1. Prompt to insert card. Detect serial and model (`ykman list --serials`, `ykman --device S info`). Exactly one card must be connected; otherwise ask to remove the extras.
2. Confirm destructive reset by typing `RESET <serial>` (CLI) or the serial into a confirmation field (GUI).
3. `ykman --device S openpgp reset -f`.
4. Change Admin then User PIN from defaults via `gpg --change-pin` (`--command-fd 0`); set retries.
5. Set key attributes via `gpg --edit-card` → `admin` → `key-attr` for slots 1–3.
6. Set login, name, URL via `--edit-card`.
7. Copy staging into a fresh per-card home; in it, `keytocard` for the S, E, A subkeys into slots 1, 2, 3 (README "Transfer Subkeys" sequence); then `gpgconf --kill all` for that home and delete it.
8. `ykman openpgp keys set-touch` per slot.
9. Verify in a second fresh home (public key imported + `gpg --card-status` to create stubs):
   - card fingerprints for slots 1–3 equal the intended subkeys;
   - sign/verify and encrypt/decrypt a random test blob using the User PIN via loopback (touch prompts shown to the user when touch is on);
   - SSH public key from the card (`gpg --export-ssh-key`) matches the auth subkey.
10. Mark card `OK` or `FAILED(step, gpg/ykman stderr)`; delete verify home.

Retry restarts the card at step 3 from staging, so retries are always safe. Skip moves on and the card stays `FAILED`.

### 6. Finish

- Export `public-$KEY_ID-<date>.asc` and `ssh-$KEY_ID.pub` to the output folder.
- Show PINs once (per card if different). PINs are never written to disk or logs.
- Print a summary table (serial, status) and the `update-stubs` instructions.
- Clean up staging.

### 7. `update-stubs` (run on the daily machine)

- Optionally import a public key file (needed after `renew`/`rotate`).
- For each new card inserted: `gpg-connect-agent "scd serialno" "learn --force" /bye`, then show `gpg --card-status` summary.
- `--remove-serial S`: delete shadowed-key stubs whose card serial is S (damaged cards), using keygrips from `gpg --with-keygrip --list-secret-keys`, after confirmation. Files are moved to a timestamped backup folder, not deleted.

## Interfaces

### Command line (identical for `python3 -m ykrecover --cli` and `recoverCard.sh`)

```text
migrate options:
  --backup DIR             backup folder (required)
  --mode clone|renew|rotate
  --cards N                number of cards (default 1)
  --out DIR                output folder for new backups/public keys
  --expire SPEC            renew/rotate expiry (default 2y)
  --algo rsa4096|ed25519   rotate only (default: match existing subkeys)
  --revoke-old             rotate only
  --pins generate|enter    (default generate)
  --same-pins
  --retries U,R,A          (default 5,5,5)
  --touch POLICY | --touch-sig/--touch-enc/--touch-aut POLICY  (default on)
  --name NAME --login LOGIN --url URL
  --dry-run                print the plan and exit 0; touches nothing
  --yes                    skip non-destructive confirmations (never the reset confirmation)

update-stubs [--import FILE] [--remove-serial S ...]
```

Secrets (Certify passphrase, entered PINs) are always read interactively from the terminal without echo — never from arguments or environment variables.

Exit codes: `0` all cards OK · `1` usage or preflight error · `2` backup or passphrase error · `3` at least one card failed or skipped · `130` interrupted.

### Dry-run plan format

One step per line, canonical and diffable:

```text
STEP <n> <phase> <tool> <args…>
STDIN <line>          # for --command-fd scripts, secrets replaced by <CERTIFY_PASS>, <ADMIN_PIN>, <USER_PIN>, <DEFAULT_ADMIN_PIN>…
```

Paths are normalized to `<STAGING>`, `<CARDHOME>`, `<BACKUP>`, `<OUT>`; serials to `<SERIAL1>`… Both implementations must produce byte-identical plans for the same inputs.

### GUI (Tkinter wizard)

Pages: Preflight → Backup (folder picker, detected format, passphrase) → Confirm key → Mode (with rotate guidance) → Card settings → Output folder (+ forced "new backup saved" confirmation for renew/rotate) → Cards (table: serial, status, current step; Retry/Skip on failure) → Summary (PINs reveal-once, file list, update-stubs instructions).

- Long operations run in a worker thread; progress and log lines go to the UI through a `queue.Queue` polled with `after()`.
- Log pane shows commands with secrets masked.
- Cancel or window close triggers cleanup (kill agents, delete homes) before exit.

## Secret handling

- Secrets go to `gpg` only via stdin pipes (`--passphrase-fd 0`, `--command-fd 0`), never argv or env, so they are not visible in `/proc/<pid>/cmdline` or `environ`.
- `ykman` calls that need the Admin PIN (`set-retries`, `set-touch`) should receive it via ykman's prompt on stdin rather than `-a`. The first implementation task verifies whether the installed ykman reads that prompt from stdin when no TTY is attached (the GUI case). If it does not, use `-a` (as the README does) and document the brief argv exposure in `recovery/README.md`; this is acceptable only because the tool targets single-user air-gapped sessions.
- Python holds secrets in `str` only as long as needed and never logs them; bash reads with `read -rs` into local variables, `unset`s them after use, and runs with `set +x` enforced.
- All temporary homes are under `$XDG_RUNTIME_DIR` (tmpfs). Cleanup: Python `try/finally` + `atexit` + signal handlers; bash `trap cleanup EXIT INT TERM`.

## Error handling

- Every external call's exit status and stderr are captured; a failure stops the current card and reports `FAILED(step, message)`.
- Backup and staging failures abort the whole run (exit 2) before any card is touched.
- No step modifies the backup folder; the output folder must not equal or contain the backup folder.
- Removing a card mid-step fails that card; Retry is offered after reinsertion.

## Testing

1. **Real-gpg tests (pytest + bats).** Throwaway keys generated per test in temp homes (both RSA and ed25519 profiles). Cover format detection for all three source kinds and ambiguous folders, passphrase checking, `renew`/`rotate` results, new-backup export, output-folder guards. Run against both implementations.
2. **Plan parity tests.** For a matrix of options, `--dry-run` output from Python and bash must be byte-identical, and match a checked-in golden file per case.
3. **Fake hardware.** Stub `ykman` and `gpg-connect-agent` scripts placed first on `PATH` that record calls and can be told to fail at a given step. They exercise the card loop, retry/skip and exit codes. `keytocard` is not exercised here.
4. **Manual hardware checklist** in `recovery/README.md`, run on a spare YubiKey before using real backups: clone, renew and rotate each on a throwaway key, then `update-stubs`.

CI (`.github/workflows/lint.yml`, extended): `pytest`, `bats`, `shellcheck --severity=error` including `recovery/recoverCard.sh`, plus the existing Markdown checks for `recovery/README.md`.

## Documentation

- `recovery/README.md`: purpose, safety warnings, LiveCD and regular-Linux usage, CLI reference, manual hardware checklist.
- Main `README.md`: one short pointer in the "Using multiple YubiKeys" or "Reset YubiKey" area linking to `recovery/README.md`, with the ToC kept in sync.
