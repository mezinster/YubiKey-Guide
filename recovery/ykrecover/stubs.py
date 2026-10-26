"""update-stubs: point the daily keyring at the new cards.

This is the only part of the toolset that touches the user's own GNUPGHOME.
"""

import datetime
import re
import shutil
import tempfile
from pathlib import Path

from . import EXIT_CARD, EXIT_INTERRUPT, EXIT_OK, EXIT_USAGE, Aborted, RecoveryError, osdeps


def token_serials(path: Path) -> list:
    """The card serials (AID characters 21-28) of a shadowed-key file's Token lines."""
    serials = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.split()
        if len(parts) > 1 and parts[0] == "Token:" and len(parts[1]) >= 28:
            serials.append(parts[1][20:28].upper())
    return serials


def find_stubs(home: Path, serial: str) -> list:
    """Shadowed-key files whose Token line carries the card serial in its AID."""
    wanted = serial.zfill(8)
    return [path for path in sorted((home / "private-keys-v1.d").glob("*.key")) if wanted in token_serials(path)]


def card_keys(learned: str) -> tuple:
    """(card serial, keygrips of its OpenPGP keys) from `scd learn --force` status lines; ("", set()) when absent."""
    serial, grips = "", set()
    for line in learned.splitlines():
        f = line.split()
        if len(f) > 2 and f[:2] == ["S", "SERIALNO"] and len(f[2]) >= 28:
            serial = f[2][20:28].upper()
        elif len(f) > 3 and f[:2] == ["S", "KEYPAIRINFO"] and re.fullmatch(r"[0-9A-Fa-f]{40}", f[2]) \
                and f[3].startswith("OPENPGP."):
            grips.add(f[2].upper())
    return serial, grips


def stale_stubs(home: Path, serial: str, grips: set) -> list:
    """Stubs that point at this card alone, for keys it no longer holds (a slot written over since)."""
    return [path for path in find_stubs(home, serial)
            if path.stem.upper() not in grips and set(token_serials(path)) == {serial.zfill(8)}]


def move_stubs(home: Path, stubs: list) -> Path:
    """Moves the stubs into a new stub-backup-* folder in home; never deletes them."""
    moved = []
    try:
        dest = Path(tempfile.mkdtemp(prefix=f"stub-backup-{datetime.datetime.now():%Y%m%d-%H%M%S}-", dir=home))
        for path in stubs:
            shutil.move(str(path), str(dest / path.name))
            moved.append(path.name)
    except OSError as err:
        left = [p.name for p in stubs if p.name not in moved]
        raise RecoveryError(
            f"moving stubs failed: {err}; moved: {' '.join(moved) or 'none'}; "
            f"not moved: {' '.join(left)}", EXIT_USAGE) from err
    return dest


def update_stubs(ui, import_file=None, remove_serials=(), cards=1) -> int:
    home = osdeps.user_gnupg_home()
    exit_code = EXIT_OK
    try:
        for serial in remove_serials:
            if not serial.isascii() or not serial.isdigit():
                raise RecoveryError(f"--remove-serial must be all digits: {serial}", EXIT_USAGE)
        missing = [t for t in ("gpg", "gpg-connect-agent") if not osdeps.available(t)]
        if missing:
            raise RecoveryError(f"missing tools: {' '.join(missing)}", EXIT_USAGE)
        if import_file:
            result = osdeps.run(["gpg", "--batch", "--import", str(import_file)])
            if result.returncode != 0:
                raise RecoveryError(f"import failed: {result.stderr.strip()}", EXIT_USAGE)
        for serial in remove_serials:
            try:
                stubs = find_stubs(home, serial)
            except OSError as err:
                raise RecoveryError(f"cannot read stubs in {home}: {err}", EXIT_USAGE) from err
            if not stubs:
                ui.info(f"no stubs reference card {serial}")
                continue
            names = "".join(f"\n  {p.name}" for p in stubs)
            if not ui.confirm(f"Move {len(stubs)} stub(s) for card {serial} out of the keyring?{names}\n"):
                continue
            ui.info(f"moved to {move_stubs(home, stubs)}")
        for index in range(1, cards + 1):
            ui.wait_for_card(index, cards)
            learn = osdeps.run(["gpg-connect-agent", "scd serialno", "scd learn --force", "learn --force", "/bye"])
            # gpg-connect-agent exits 0 even when a command answers ERR, so read its output too.
            errors = [line for line in learn.stdout.splitlines() if line.startswith("ERR")]
            if learn.returncode != 0 or errors:
                detail = errors[0] if errors else learn.stderr.strip()
                ui.warn(f"learn failed for card {index}: {detail}")
                exit_code = EXIT_CARD
            else:
                # a slot written over leaves the stub of its old key, still naming this card (gpg never removes it)
                serial, grips = card_keys(learn.stdout)
                try:
                    stale = stale_stubs(home, serial, grips) if serial and grips else []
                except OSError as err:
                    raise RecoveryError(f"cannot read stubs in {home}: {err}", EXIT_USAGE) from err
                names = "".join(f"\n  {p.name}" for p in stale)
                if stale and ui.confirm(f"Move {len(stale)} stub(s) for keys no longer on card {serial} out of "
                                        f"the keyring?{names}\n"):
                    ui.info(f"moved to {move_stubs(home, stale)}")
            status = osdeps.run(["gpg", "--batch", "--card-status"])
            if status.returncode != 0:
                ui.warn(f"card status failed for card {index}: {status.stderr.strip()}")
                exit_code = EXIT_CARD
            else:
                ui.info(status.stdout.strip())
        return exit_code
    except RecoveryError as err:
        ui.error(str(err))
        return err.code
    except (Aborted, KeyboardInterrupt):
        ui.error("aborted")
        return EXIT_INTERRUPT
