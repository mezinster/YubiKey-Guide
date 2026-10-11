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
