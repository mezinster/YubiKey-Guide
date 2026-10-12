import os
import shutil
import subprocess
import unittest

from ykrecover import osdeps
from tests.helpers import FIXTURES, LINUX, run_impl, short_tempdir

ED_EXPORT = str(FIXTURES / "ed25519" / "export")


class ContractTests:
    """Option validation and dry-run behaviour shared by both implementations."""

    IMPL = ""

    def cli(self, *args, stdin="", env=None):
        return run_impl(self.IMPL, list(args), stdin, env)

    def assert_fails(self, result, code, message):
        """Exit code and the error text (both implementations print the same text, or a usage message)."""
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertIn(message, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_missing_backup_is_usage_error(self):
        self.assert_fails(self.cli("--mode", "clone"), 1, "usage:")

    def test_bad_choice_is_usage_error(self):
        self.assert_fails(self.cli("--backup", ED_EXPORT, "--mode", "bogus", "--dry-run"), 1, "usage:")
        self.assert_fails(self.cli("--backup", ED_EXPORT, "--retries", "0,5,5", "--dry-run"), 1, "retries")
        self.assert_fails(self.cli("--backup", ED_EXPORT, "--cards", "0", "--dry-run"), 1,
                          "error: --cards must be 1 or more")

    def test_cardholder_fields_validated(self):
        for flag, value in (("--name", "J" * 40), ("--name", "Jöhn"), ("--name", "a<b"), ("--login", "x" * 255),
                            ("--url", "https://example.org/\tkey"), ("--url", "u" * 255)):
            with self.subTest(flag=flag, value=value):
                result = self.cli("--backup", ED_EXPORT, flag, value, "--dry-run")
                self.assert_fails(result, 1, f"error: {flag} must be at most")
        result = self.cli("--backup", ED_EXPORT, "--name", "J" * 39, "--login", "x" * 254, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_abbreviated_options_rejected(self):
        self.assert_fails(self.cli("--back", ED_EXPORT, "--dry-run"), 1, "usage:")
        self.assert_fails(self.cli("--backup", ED_EXPORT, "--dry"), 1, "usage:")
        self.assert_fails(self.cli("update-stubs", "--remove", "1234"), 1, "usage:")

    def test_leading_zero_numbers_are_decimal(self):
        r = self.cli("--backup", ED_EXPORT, "--cards", "010", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(" card10 ", r.stdout)
        self.assertNotIn(" card11 ", r.stdout)
        self.assertEqual(self.cli("--backup", ED_EXPORT, "--cards", "08", "--dry-run").returncode, 0)
        self.assertEqual(self.cli("--backup", ED_EXPORT, "--retries", "005,5,5", "--dry-run").returncode, 0)

    def test_algo_requires_rotate(self):
        self.assert_fails(self.cli("--backup", ED_EXPORT, "--algo", "ed25519", "--dry-run"), 1,
                          "error: --algo and --revoke-old need --mode rotate")

    def test_out_required_for_live_run(self):
        self.assert_fails(self.cli("--backup", ED_EXPORT), 1, "error: --out is required")

    def test_out_inside_backup_rejected(self):
        self.assert_fails(self.cli("--backup", ED_EXPORT, "--out", f"{ED_EXPORT}/new"), 1,
                          "error: --out must be outside the backup folder")

    def test_out_with_existing_keys_rejected(self):
        out = short_tempdir(self, "out")
        (out / "secret-0000-Certify.key").write_text("old")
        self.assert_fails(self.cli("--backup", ED_EXPORT, "--out", str(out)), 1,
                          "already holds key files; choose an empty folder")

    def test_backup_not_found(self):
        self.assert_fails(self.cli("--backup", "/nonexistent/backup", "--dry-run"), 2,
                          "error: backup folder not found: ")

    def test_ambiguous_backup_needs_source(self):
        root = FIXTURES / "ed25519"
        self.assert_fails(self.cli("--backup", str(root), "--dry-run"), 2,
                          "error: several backups found; narrow --backup or pass --source or --key:")
        result = self.cli("--backup", str(root), "--source", "gnupghome", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_subkeys_only_cannot_renew(self):
        result = self.cli("--backup", str(FIXTURES / "ed25519" / "subkeys-only"), "--mode", "renew", "--dry-run")
        self.assert_fails(result, 2, "error: --mode renew needs the Certify key, but this backup only has subkeys")

    def test_expired_needs_renew(self):
        expired = str(FIXTURES / "expired" / "export")
        self.assert_fails(self.cli("--backup", expired, "--dry-run"), 2, "error: subkeys have expired: ")
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
        osdeps.run(["gpgconf", "--kill", "all"], env={**os.environ, "GNUPGHOME": str(copy)})
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

    @unittest.skipUnless(LINUX, "POSIX directory modes and XDG_RUNTIME_DIR")
    def test_unwritable_runtime_dir_is_a_staging_error(self):
        base = short_tempdir(self, "rt")
        base.chmod(0o500)
        result = self.cli("--backup", ED_EXPORT, "--dry-run", env={"XDG_RUNTIME_DIR": str(base)})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("cannot create a temporary GnuPG home", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_read_only_backup(self):
        copy = short_tempdir(self, "ro") / "backup"
        shutil.copytree(FIXTURES / "ed25519" / "gnupghome", copy)
        for p in [copy, *copy.rglob("*")]:
            p.chmod(0o500 if p.is_dir() else 0o400)
        result = self.cli("--backup", str(copy), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_workdir_must_exist_and_be_short(self):
        self.assert_fails(self.cli("--backup", ED_EXPORT, "--workdir", "/nonexistent/dir", "--dry-run"), 1,
                          "error: --workdir must be an existing folder: ")
        ok = short_tempdir(self, "wd")
        result = self.cli("--backup", ED_EXPORT, "--workdir", str(ok), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        if LINUX:
            deep = ok / ("d" * 70)
            deep.mkdir()
            self.assert_fails(self.cli("--backup", ED_EXPORT, "--workdir", str(deep), "--dry-run"), 1,
                              "error: --workdir path is too long for gpg-agent sockets")

    def test_dry_run_prints_only_the_plan(self):
        result = self.cli("--backup", ED_EXPORT, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("STEP 1 card1 <CARDHOME> gpgconf --kill all\n"))


class PythonContractTests(ContractTests, unittest.TestCase):
    IMPL = "python"


@unittest.skipUnless(LINUX, "the bash implementation is Linux-only")
class BashContractTests(ContractTests, unittest.TestCase):
    IMPL = "bash"

    @unittest.expectedFailure  # bash still prints the old message; Task 8 removes this marker
    def test_ambiguous_backup_needs_source(self):
        super().test_ambiguous_backup_needs_source()


if __name__ == "__main__":
    unittest.main()
