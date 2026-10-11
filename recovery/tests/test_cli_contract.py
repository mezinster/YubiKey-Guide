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
