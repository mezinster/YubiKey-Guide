import unittest

from tests.helpers import FIXTURES, fixture_info


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


if __name__ == "__main__":
    unittest.main()
