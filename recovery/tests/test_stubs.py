import shutil
import unittest
from pathlib import Path

from tests.helpers import FIXTURES, LINUX, fake_hardware, fixture_info, run_impl, short_tempdir

OLD, OTHER = "22222222", "33333333"


def stub(serial, slot, *others):
    tokens = "".join(f"Token: D2760001240103040006{s}0000 OPENPGP.{slot} - - -\n" for s in (serial, *others))
    return f"Key: (shadowed-private-key (ecc (curve Ed25519)))\n{tokens}"


NEW = "44444444"
ON_CARD, STALE, SHARED = "E" * 40, "F" * 40, "1" * 40


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
        self.assertIn("gpg-connect-agent scd serialno | scd learn --force | learn --force | /bye", calls)

    def stale_setup(self):
        """Card NEW holds ON_CARD; STALE points at NEW only, SHARED at NEW and OTHER."""
        keys = self.home / "private-keys-v1.d"
        (keys / f"{ON_CARD}.key").write_text(stub(NEW, 2))
        (keys / f"{STALE}.key").write_text(stub(NEW, 2))
        (keys / f"{SHARED}.key").write_text(stub(NEW, 3, OTHER))
        (Path(self.env["FAKE_HW_DIR"]) / "learn.txt").write_text(
            f"S SERIALNO D2760001240103040006{NEW}0000\n"
            f"S KEYPAIRINFO {ON_CARD} OPENPGP.2 e 1791717024 rsa2048\n")
        return keys

    def test_moves_stubs_of_keys_no_longer_on_the_card(self):
        keys = self.stale_setup()
        result = self.run_stubs([], "\ny\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"no longer on card {NEW}", result.stderr)
        self.assertEqual(sorted(p.name for p in keys.iterdir()),
                         sorted(["AAAA.key", "BBBB.key", "CCCC.key", f"{ON_CARD}.key", f"{SHARED}.key"]))
        (backup_dir,) = self.home.glob("stub-backup-*")
        self.assertEqual([p.name for p in backup_dir.iterdir()], [f"{STALE}.key"])

    def test_declining_keeps_stale_stubs(self):
        keys = self.stale_setup()
        result = self.run_stubs([], "\nn\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((keys / f"{STALE}.key").exists())
        self.assertEqual(list(self.home.glob("stub-backup-*")), [])

    def test_card_listing_no_keys_moves_nothing(self):
        keys = self.stale_setup()
        (Path(self.env["FAKE_HW_DIR"]) / "learn.txt").write_text(f"S SERIALNO D2760001240103040006{NEW}0000\n")
        result = self.run_stubs([], "\n")  # no question asked: one more would end the input and abort
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(keys.iterdir())), 6)

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

    def test_import_failure_shows_the_gpg_error(self):
        bogus = self.home / "not-a-key.asc"
        bogus.write_text("not a key\n")
        result = self.run_stubs(["--import", str(bogus)], "\n")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("import failed: gpg: ", result.stderr)
        self.assertIn("no valid OpenPGP data found", result.stderr)

    def test_eof_aborts(self):
        self.assertEqual(self.run_stubs([], "").returncode, 130)


class PythonStubsTests(StubsTests, unittest.TestCase):
    IMPL = "python"


@unittest.skipUnless(LINUX, "the bash implementation is Linux-only")
class BashStubsTests(StubsTests, unittest.TestCase):
    IMPL = "bash"



@unittest.skipUnless(LINUX, "the fake gpgconf is a shell script")
class GnupgHomeDecodingTests(unittest.TestCase):
    """Both implementations decode gpgconf's homedir the same way: only %XX is an escape."""

    def test_percent_in_the_reported_home(self):
        base = short_tempdir(self, "pct")
        fakebin = base / "bin"
        fakebin.mkdir()
        gpgconf = fakebin / "gpgconf"
        gpgconf.write_text('#!/bin/sh\nif [ "$#" = 2 ] && [ "$1" = --list-dirs ] && [ "$2" = homedir ]; then\n'
                           '  printf \'%s\\n\' "$FAKE_HOMEDIR"; exit 0\nfi\n'
                           f'exec {shutil.which("gpgconf")} "$@"\n')
        gpgconf.chmod(0o755)
        for listed, actual in (("100%done", "100%done"), ("a%3ab", "a:b")):
            for impl in ("python", "bash"):
                with self.subTest(listed=listed, impl=impl):
                    home = base / f"{impl}-{actual}"
                    keys = home / "private-keys-v1.d"
                    keys.mkdir(parents=True, mode=0o700)
                    home.chmod(0o700)
                    (keys / "AAAA.key").write_text(stub(OLD, 1))
                    env = fake_hardware(self, ["44444444"])
                    env.update(PATH=f"{fakebin}:{env['PATH']}", GNUPGHOME=str(base / "unused"),
                               FAKE_HOMEDIR=str(base / f"{impl}-{listed}"))
                    result = run_impl(impl, ["update-stubs", "--remove-serial", OLD], "y\n\n", env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(list(keys.iterdir()), [])
                    self.assertEqual(len(list(home.glob("stub-backup-*/AAAA.key"))), 1)


if __name__ == "__main__":
    unittest.main()
