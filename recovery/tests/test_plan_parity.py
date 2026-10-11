import os
import sys
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


LINUX = sys.platform.startswith("linux")


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


@unittest.skipUnless(LINUX, "the bash implementation is Linux-only")
class BashPlanParityTests(PlanParityTests, unittest.TestCase):
    IMPL = "bash"


if __name__ == "__main__":
    unittest.main()
