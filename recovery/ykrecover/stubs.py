"""update-stubs: point the daily keyring at the new cards.

This is the only part of the toolset that touches the user's own GNUPGHOME.
"""

import datetime
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import EXIT_CARD, EXIT_INTERRUPT, EXIT_OK, EXIT_USAGE, Aborted, RecoveryError


def user_home() -> Path:
    return Path(os.environ.get("GNUPGHOME") or Path.home() / ".gnupg")


def find_stubs(home: Path, serial: str) -> list:
    """Shadowed-key files whose Token line carries the card serial in its AID."""
    wanted = serial.zfill(8)
    found = []
    for path in sorted((home / "private-keys-v1.d").glob("*.key")):
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split()
            if len(parts) > 1 and parts[0] == "Token:" and len(parts[1]) >= 28 \
                    and parts[1][20:28].upper() == wanted:
                found.append(path)
                break
    return found


def update_stubs(ui, import_file=None, remove_serials=(), cards=1) -> int:
    home = user_home()
    exit_code = EXIT_OK
    try:
        for serial in remove_serials:
            if not serial.isascii() or not serial.isdigit():
                raise RecoveryError(f"--remove-serial must be all digits: {serial}", EXIT_USAGE)
        missing = [t for t in ("gpg", "gpg-connect-agent") if shutil.which(t) is None]
        if missing:
            raise RecoveryError(f"missing tools: {' '.join(missing)}", EXIT_USAGE)
        if import_file:
            result = subprocess.run(["gpg", "--batch", "--import", str(import_file)],
                                    capture_output=True, text=True, check=False)
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
            moved = []
            try:
                dest = Path(tempfile.mkdtemp(
                    prefix=f"stub-backup-{datetime.datetime.now():%Y%m%d-%H%M%S}-", dir=home))
                for path in stubs:
                    shutil.move(str(path), str(dest / path.name))
                    moved.append(path.name)
            except OSError as err:
                left = [p.name for p in stubs if p.name not in moved]
                raise RecoveryError(
                    f"moving stubs failed: {err}; moved: {' '.join(moved) or 'none'}; "
                    f"not moved: {' '.join(left)}", EXIT_USAGE) from err
            ui.info(f"moved to {dest}")
        for index in range(1, cards + 1):
            ui.wait_for_card(index, cards)
            learn = subprocess.run(["gpg-connect-agent", "scd serialno", "learn --force", "/bye"],
                                   capture_output=True, text=True, check=False)
            # gpg-connect-agent exits 0 even when a command answers ERR, so read its output too.
            errors = [line for line in learn.stdout.splitlines() if line.startswith("ERR")]
            if learn.returncode != 0 or errors:
                detail = errors[0] if errors else learn.stderr.strip()
                ui.warn(f"learn failed for card {index}: {detail}")
                exit_code = EXIT_CARD
            status = subprocess.run(["gpg", "--batch", "--card-status"], capture_output=True, text=True, check=False)
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
