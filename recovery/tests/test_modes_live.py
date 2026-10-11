import time
import unittest

from tests.helpers import FIXTURES, PASSPHRASE, fixture_info, short_tempdir
from ykrecover import backup
from ykrecover.gpg import Home
from ykrecover.migrate import Options, Session


class SilentUI:
    def warn(self, msg): pass
    def info(self, msg): pass


class ApplyModeTests(unittest.TestCase):
    def session(self, profile, **opts):
        out = short_tempdir(self, "out")
        s = Session(Options(backup=FIXTURES / profile / "export", out=out, **opts), SilentUI())
        self.addCleanup(s.cleanup)
        s.load()
        self.assertTrue(s.unlock(PASSPHRASE))
        return s, out

    def exported(self, path):
        home = Home.create("check")
        self.addCleanup(home.destroy)
        home.run(["gpg", "--batch", "--import", str(path)])
        return backup.choose_key(backup.list_bundles(home))

    def test_renew_extends_expiry_and_writes_backup(self):
        info = fixture_info("expired")
        s, out = self.session("expired", mode="renew", expire="3y")
        s.apply_mode()
        self.assertEqual(s.targets["s"], info["FPR_S"])
        kid = info["KEY_ID"]
        for name in (f"secret-{kid}-Certify.key", f"secret-{kid}-Subkeys.key"):
            self.assertTrue((out / name).is_file(), name)
        renewed = self.exported(out / f"secret-{kid}-Certify.key")
        self.assertTrue(all(k.expires > time.time() for k in renewed.subkeys))

    def test_rotate_with_revoke(self):
        info = fixture_info("rsa4096")
        s, out = self.session("rsa4096", mode="rotate", algo="ed25519", revoke_old=True)
        s.apply_mode()
        self.assertNotIn(s.targets["s"], {info["FPR_S"], info["FPR_E"], info["FPR_A"]})
        rotated = self.exported(out / f"secret-{info['KEY_ID']}-Certify.key")
        by_fpr = {k.fpr: k for k in rotated.subkeys}
        self.assertEqual({by_fpr[info[f]].validity for f in ("FPR_S", "FPR_E", "FPR_A")}, {"r"})
        self.assertEqual(by_fpr[s.targets["e"]].algo, "cv25519")

    def test_clone_changes_nothing(self):
        info = fixture_info("ed25519")
        s, out = self.session("ed25519")
        s.apply_mode()
        self.assertEqual(s.targets, {"s": info["FPR_S"], "e": info["FPR_E"], "a": info["FPR_A"]})
        self.assertEqual(list(out.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
