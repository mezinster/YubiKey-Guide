# YubiKey recovery

Migrate GnuPG key backups, made with this guide before `keytocard`, onto new YubiKeys. Use it when existing YubiKeys are damaged, lost or being replaced.

Two equivalent tools:

- `yubikey-recovery`: a graphical wizard (`python3 -m ykrecover` from this folder). Add `--cli` for the same flow in a terminal.
- `recoverCard.sh`: the same options, steps and exit codes in bash.

On the LiveCD built from `nix/` both are installed, and "YubiKey Recovery" is on the desktop.

The Python tool runs on Linux and Windows; the bash script is Linux-only. On other systems both exit with an error.

**Windows:** install [Gpg4win](https://gpg4win.org), then download `ykrecover-windows-x64.zip` from the repository's Releases page (or the `windows` CI job's artifacts) and run `ykrecover-gui.exe`, or `ykrecover.exe --cli …` in a terminal. ykman is bundled. Windows has no memory-backed temporary folder, so the tool warns that key copies touch the disk while it runs; to avoid that, choose a RAM disk or an encrypted volume as the work folder (`--workdir DIR`, or "Work folder" in the wizard). If a run ends abnormally (the console window closed, the process ended in Task Manager, a crash), its temporary folders (`ykr-*` in the work folder) can stay behind, not overwritten: the next run warns about any older than 10 minutes, and you should delete them securely. Run the hardware checklist on Windows too: card access works differently there. To build locally on Windows (Python 3.11+ on PATH), run `powershell -NoProfile -ExecutionPolicy Bypass -File recovery\packaging\build-windows.ps1` from cmd or PowerShell; the zip appears in `recovery\packaging\dist`.

Run the Python tool as a package from the `recovery` folder: `python3 -m ykrecover`. Running `ykrecover/gui.py` directly fails with "attempted relative import".

> [!WARNING]
> Every card you write is **reset**: all OpenPGP keys and PINs on it are erased. The tool asks you to type `RESET <serial>` for each card.

> [!IMPORTANT]
> Run this on an air-gapped system. Your backup is copied into temporary GnuPG homes under `$XDG_RUNTIME_DIR` (or `/tmp` when that path is unset or too long), which are deleted when the tool exits. They are memory-backed only when that folder is on tmpfs, as on the LiveCD; otherwise the tool prints a warning, and key material may reach the disk. On Windows the default is `%LOCALAPPDATA%\Temp\ykrecover`, which is on disk: the files are overwritten before deletion, but choose a RAM disk or encrypted volume with `--workdir` to keep key material off the disk.

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

`--out` is required for every run except `--dry-run`, `clone` included: the public key (`public-KEYID-DATE.asc`) and the SSH public key (`ssh-KEYID.pub`) are written there, and for `renew` and `rotate` the new backup too. It must be outside the backup folder and must not already hold `secret-*.key` files.

A backup made with the main guide often holds both the exported key files (`secret-*-Certify.key`, `secret-*-Subkeys.key`) and a copy of the GnuPG home. The tool then stops and asks you to choose with `--source`. Pass `--source export`: the exports were made before `keytocard` and hold the secret subkeys, while a GnuPG home copied after `keytocard` holds only card stubs, which the tool refuses for `clone` and `renew`.

`--workdir DIR`: where temporary GnuPG homes go (must exist; on Linux at most 60 characters)

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

`--remove-serial` takes the decimal YubiKey serial (digits only, as printed on the card or by `ykman list`) and matches it against the serial field of each stub's card ID. Matching stubs are moved into a new `stub-backup-*` folder in the GnuPG home, never deleted. The command exits with 3 if `learn` fails for a card.

If you renewed or rotated, also publish the new public key wherever you published the old one.

## How it works

1. The backup is imported into a temporary staging GnuPG home. The backup itself is only read.
2. The mode is applied in staging.
3. For each card, staging is copied into a fresh home. The card is reset; the PIN retry counters are set while the PINs are still default (changing the retry counters can reset the PINs); the PINs are changed; cardholder data is set; `keytocard` moves the subkeys; the touch policy is set; and then that copy is deleted. Staging is never consumed, so a failed card can be retried safely. Before a retry, the tool checks that the same card is still the only one connected, and asks you to reconnect it otherwise.
4. A third home holding only the public key verifies the card:
   - the card's fingerprints match the subkeys;
   - a test signature verifies;
   - a test message decrypts.

   With touch on (the default), the tool prints "Touch the YubiKey when it blinks" before the test signature and before the decryption: touch the card each time, or the step times out and the card fails.

The PINs of every card whose PINs were changed are shown once, at the end, including cards that failed or were aborted and runs stopped by Ctrl-C, SIGTERM, SIGHUP or (on Windows) Ctrl-Break, so a partially written card is never left with unknown PINs. The GUI shows them as soon as card writing finishes. Write them down then.

Both tools run with umask 077, so everything written to `--out` is readable only by its owner.

Before every card session (each scripted `gpg --change-pin`, `--edit-card` or `--edit-key` run) and every ykman command, `gpgconf --kill all` clears cached passphrases and card sessions, so prompts always arrive in the expected order.

The Admin PIN is passed to `ykman` with `-a`, as in the main guide. On a multi-user machine other users could briefly see it in the process list; on the intended single-user air-gapped system that risk does not apply. On Windows, which is usually an everyday and possibly managed machine, process-creation auditing or endpoint security software can record command lines, including the Admin PIN: on a managed or audited machine, change the Admin PIN afterwards (`ykman openpgp access change-admin-pin`).

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

- [ ] `clone` from `tests/fixtures/ed25519/export` to one card, with the GUI: "Touch the YubiKey when it blinks" appears twice during verification, the card blinks each time, and after touching it the card shows OK. `gpg --card-status` lists the three fixture fingerprints and `Login data` starts with `yk.`.
- [ ] `clone` from `tests/fixtures/rsa4096/export` with `recoverCard.sh --touch off`: OK, and the key attributes show `rsa4096` for all three slots.
- [ ] `renew` from `tests/fixtures/expired/export`: a new backup appears in `--out`, and the card is OK.
- [ ] `rotate --algo ed25519 --revoke-old` from `tests/fixtures/rsa4096/export`: the card's fingerprints differ from the fixture's, and the exported public key shows the old subkeys as revoked.
- [ ] Pull the card out during the `keytocard` steps: the card is FAILED with gpg's message; reinsert it, choose Retry, and it succeeds.
- [ ] `update-stubs --remove-serial <spare serial>` on a test GNUPGHOME: the stubs move to `stub-backup-*`, and `gpg --card-status` lists the card again.
- [ ] Afterwards reset the spare card: `ykman openpgp reset`.
