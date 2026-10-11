import unittest
from pathlib import Path

from tests.helpers import FIXTURES, LINUX, fake_hardware, fixture_info, run_impl, short_tempdir

OLD, OTHER = "22222222", "33333333"


def stub(serial, slot):
    return f"Key: (shadowed-private-key (ecc (curve Ed25519)))\nToken: D2760001240103040006{serial}0000 OPENPGP.{slot} - - -\n"


class StubsTests:
    IMPL = ""

    def setUp(self):
        self.home = short_tempdir(self, "home")
        self.home.chmod(0o700)
        keys = self.home / "private-keys-v1.d"
        keys.mkdir(mode=0o700)
        (keys / "AAAA.key").write_text(stub(OLD, 1))
        (keys / "BBBB.key").write_text(stub(OLD, 2))
        (keys / "CCCC.key").write_text(stub(OTHER, 1))
        self.env = {**fake_hardware(self, ["44444444"]), "GNUPGHOME": str(self.home)}

    def run_stubs(self, args, stdin):
        return run_impl(self.IMPL, ["update-stubs", *args], stdin, self.env)

    def test_moves_old_stubs_and_learns_new_card(self):
        result = self.run_stubs(["--remove-serial", OLD], "y\n\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        remaining = sorted(p.name for p in (self.home / "private-keys-v1.d").iterdir())
        self.assertEqual(remaining, ["CCCC.key"])
        (backup_dir,) = self.home.glob("stub-backup-*")
        self.assertEqual(sorted(p.name for p in backup_dir.iterdir()), ["AAAA.key", "BBBB.key"])
        calls = (Path(self.env["FAKE_HW_DIR"]) / "calls.log").read_text()
        self.assertIn("gpg-connect-agent scd serialno | learn --force | /bye", calls)

    def test_declining_keeps_stubs(self):
        result = self.run_stubs(["--remove-serial", OLD], "n\n\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list((self.home / "private-keys-v1.d").iterdir())), 3)
        self.assertEqual(list(self.home.glob("stub-backup-*")), [])

    def test_serial_must_match_aid_field(self):
        keys = self.home / "private-keys-v1.d"
        (keys / "DDDD.key").write_text(stub("99992222", 1))
        result = self.run_stubs(["--remove-serial", OLD], "y\n\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((keys / "DDDD.key").exists())
        self.assertFalse((keys / "AAAA.key").exists())

    def test_non_numeric_serial_rejected(self):
        result = self.run_stubs(["--remove-serial", "0006X"], "y\n\n")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(len(list((self.home / "private-keys-v1.d").iterdir())), 3)

    def test_learn_failure_exits_3(self):
        self.env = {**fake_hardware(self, ["44444444"], fail_at="learn"), "GNUPGHOME": str(self.home)}
        result = self.run_stubs([], "\n")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("ERR 67108983 No SmartCard daemon", result.stderr)

    def test_card_status_failure_exits_3(self):
        self.env = {**fake_hardware(self, ["44444444"], fail_at="card-status"), "GNUPGHOME": str(self.home)}
        result = self.run_stubs([], "\n")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("card status failed for card 1", result.stderr)

    def test_import_public_key(self):
        info = fixture_info("ed25519")
        public = FIXTURES / "ed25519" / "export" / f"public-{info['KEY_ID']}-2026-10-11.asc"
        result = self.run_stubs(["--import", str(public)], "\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_eof_aborts(self):
        self.assertEqual(self.run_stubs([], "").returncode, 130)


class PythonStubsTests(StubsTests, unittest.TestCase):
    IMPL = "python"


@unittest.skipUnless(LINUX, "the bash implementation is Linux-only")
class BashStubsTests(StubsTests, unittest.TestCase):
    IMPL = "bash"


if __name__ == "__main__":
    unittest.main()
