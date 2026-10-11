"""CI smoke test for a built ykrecover executable: python smoke_test.py PATH_TO_EXE

The dry-run needs gpg and gpgconf on PATH (Gpg4win on Windows, gnupg on Linux).
"""

import subprocess
import sys
from pathlib import Path

RECOVERY = Path(__file__).resolve().parents[1]


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python smoke_test.py PATH_TO_EXE")
        return 2
    exe = sys.argv[1]
    plan = subprocess.run([exe, "--cli", "--backup", str(RECOVERY / "tests" / "fixtures" / "ed25519" / "export"),
                           "--dry-run"], capture_output=True, text=True, encoding="utf-8", check=False)
    if plan.returncode != 0:
        print(f"dry-run failed ({plan.returncode}):\n{plan.stderr}")
        return 1
    golden = (RECOVERY / "tests" / "golden" / "clone-ed25519-export.plan").read_text(encoding="utf-8")
    if plan.stdout != golden:
        print("dry-run plan differs from tests/golden/clone-ed25519-export.plan")
        return 1
    version = subprocess.run([exe, "--ykman", "--version"], capture_output=True, text=True, encoding="utf-8", check=False)
    if version.returncode != 0 or "YubiKey Manager" not in version.stdout:
        print(f"bundled ykman failed ({version.returncode}):\n{version.stdout}\n{version.stderr}")
        return 1
    print(f"smoke test passed: {version.stdout.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
