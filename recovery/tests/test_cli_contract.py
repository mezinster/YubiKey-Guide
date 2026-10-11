import os
import shutil
import subprocess
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

    def test_cardholder_fields_validated(self):
        for flag, value in (("--name", "J" * 40), ("--name", "Jöhn"), ("--name", "a<b"), ("--login", "x" * 255),
                            ("--url", "https://example.org/\tkey"), ("--url", "u" * 255)):
            with self.subTest(flag=flag, value=value):
                result = self.cli("--backup", ED_EXPORT, flag, value, "--dry-run")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout, "")
        result = self.cli("--backup", ED_EXPORT, "--name", "J" * 39, "--login", "x" * 254, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_abbreviated_options_rejected(self):
        self.assertEqual(self.cli("--back", ED_EXPORT, "--dry-run").returncode, 1)
        self.assertEqual(self.cli("--backup", ED_EXPORT, "--dry").returncode, 1)
        self.assertEqual(self.cli("update-stubs", "--remove", "1234").returncode, 1)

    def test_leading_zero_numbers_are_decimal(self):
        r = self.cli("--backup", ED_EXPORT, "--cards", "010", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(" card10 ", r.stdout)
        self.assertNotIn(" card11 ", r.stdout)
        self.assertEqual(self.cli("--backup", ED_EXPORT, "--cards", "08", "--dry-run").returncode, 0)
        self.assertEqual(self.cli("--backup", ED_EXPORT, "--retries", "005,5,5", "--dry-run").returncode, 0)

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

    def test_backup_with_card_stubs_rejected(self):
        """A GnuPG home copied after keytocard holds only stubs for the subkeys."""
        copy = short_tempdir(self, "stubs") / "backup"
        shutil.copytree(FIXTURES / "ed25519" / "gnupghome", copy)
        copy.chmod(0o700)
        listing = subprocess.run(["gpg", "--batch", "--with-colons", "--with-keygrip", "--list-secret-keys"],
                                 env={**os.environ, "GNUPGHOME": str(copy)}, capture_output=True, text=True,
                                 check=True).stdout
        grips, in_sub = [], False
        for line in listing.splitlines():
            fields = line.split(":")
            if fields[0] in ("sec", "ssb"):
                in_sub = fields[0] == "ssb"
            elif fields[0] == "grp" and in_sub:
                grips.append(fields[9])
        self.assertEqual(len(grips), 3)
        subprocess.run(["gpgconf", "--kill", "all"], env={**os.environ, "GNUPGHOME": str(copy)}, check=False)
        for grip in grips:
            (copy / "private-keys-v1.d" / f"{grip}.key").unlink()
        for mode in ("clone", "renew"):
            with self.subTest(mode=mode):
                result = self.cli("--backup", str(copy), "--mode", mode, "--dry-run")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("the backup holds card stubs, not secret subkeys:", result.stderr)
                self.assertEqual(result.stdout, "")
        # rotate writes newly generated subkeys, so stubs for the old ones do not matter
        result = self.cli("--backup", str(copy), "--mode", "rotate", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)

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


class BashContractTests(ContractTests, unittest.TestCase):
    IMPL = "bash"


if __name__ == "__main__":
    unittest.main()
