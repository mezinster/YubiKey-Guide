import os
import subprocess
import unittest

from tests.helpers import FIXTURES, REAL_GPG, fixture_info, short_tempdir


class FixtureTests(unittest.TestCase):
    def test_profiles_have_every_backup_format(self):
        for profile in ("ed25519", "rsa4096", "expired"):
            with self.subTest(profile):
                info = fixture_info(profile)
                self.assertEqual(set(info), {"KEY_FP", "KEY_ID", "FPR_S", "FPR_E", "FPR_A"})
                kid = info["KEY_ID"]
                root = FIXTURES / profile
                self.assertTrue((root / "export" / f"secret-{kid}-Certify.key").is_file())
                self.assertTrue((root / "export" / f"secret-{kid}-Subkeys.key").is_file())
                self.assertTrue((root / "subkeys-only" / f"secret-{kid}-Subkeys.key").is_file())
                self.assertTrue((root / "gnupghome" / "private-keys-v1.d").is_dir())
                self.assertTrue((root / "gnupghome" / "pubring.kbx").is_file())
                self.assertFalse(list((root / "subkeys-only").glob("*Certify*")))


class NewFixtureShapeTests(unittest.TestCase):
    def packets(self, path):
        result = subprocess.run([REAL_GPG, "--batch", "--list-packets", str(path)], capture_output=True, text=True,
                                env={**os.environ, "GNUPGHOME": str(short_tempdir(self, "pk"))}, check=False)
        return result.stdout

    def test_stub_files_are_card_stubs(self):
        folder = FIXTURES / "flexible" / "asc backup" / "stub"
        full = self.packets(folder / "flex.priv_stub.asc")
        self.assertEqual(full.count("gnu-divert-to-card"), 3)
        subs = self.packets(folder / "flex.sub_priv_stub.asc")
        self.assertEqual(subs.count("gnu-divert-to-card"), 2)
        self.assertEqual(subs.count("gnu-dummy"), 1)

    def test_split_main_file_has_dummy_subkeys(self):
        text = self.packets(FIXTURES / "split" / "a-main" / "main-only.asc")
        self.assertEqual(text.count("gnu-dummy"), 3)
        self.assertEqual(text.count("S2K"), 1)

    def test_unprotected_key_has_no_s2k(self):
        text = self.packets(FIXTURES / "unprotected" / "key.asc")
        self.assertNotIn("S2K", text)
        self.assertIn("bits]", text)


if __name__ == "__main__":
    unittest.main()
