import unittest
from pathlib import Path

from tests.helpers import FIXTURES, PASSPHRASE, fixture_info
from ykrecover import EXIT_BACKUP, RecoveryError, backup
from ykrecover.gpg import Home

COLONS_TWO_KEYS = """\
sec:u:255:22:D35238B02045AC9D:1791717022:::u:::cESCA:::+::ed25519:::0:
fpr:::::::::9D1EEF5129527741D5D71C30D35238B02045AC9D:
grp:::::::::1BD940563F0ED08451AB0FA1929B36AD4BE43F7C:
uid:u::::1791717022::230162C5BA27CB835FEFA182F7362D77F77BB1A7::Test <t@example.org>::::::::::0:
ssb:u:255:22:A3C895A8B9C0084C:1791717024:1854789024:::::s:::+::ed25519::
fpr:::::::::289425A50E5E40018F9BBD22A3C895A8B9C0084C:
grp:::::::::EEA8DE772E836F74E1BB4AC797F79DB808D4A3BD:
ssb:u:4096:1:266DF3F59D5DF0EF:1791717025::::::e:::#::::
fpr:::::::::B3B43A994B5B2F01BF225D2A266DF3F59D5DF0EF:
grp:::::::::3E79726AA188E01C2C4436695167BF9F1F9917D0:
sec:u:255:22:1111111111111111:1791717022:::u:::cESCA:::#::ed25519:::0:
fpr:::::::::AAAA1111111111111111111111111111111111111:
"""


class ParseTests(unittest.TestCase):
    def test_parses_keys_and_subkeys(self):
        first, second = backup.parse_secret_keys(COLONS_TWO_KEYS)
        self.assertEqual(first.key_fp, "9D1EEF5129527741D5D71C30D35238B02045AC9D")
        self.assertEqual(first.key_id, "D35238B02045AC9D")
        self.assertEqual(first.uids, ("Test <t@example.org>",))
        self.assertTrue(first.has_certify_secret)
        sign, enc = first.subkeys
        self.assertEqual((sign.usage, sign.algo, sign.expires), ("s", "ed25519", 1854789024))
        self.assertEqual(sign.keygrip, "EEA8DE772E836F74E1BB4AC797F79DB808D4A3BD")
        self.assertEqual((enc.usage, enc.algo, enc.expires), ("e", "rsa4096", 0))
        self.assertEqual((sign.secret, enc.secret), (True, False))
        self.assertFalse(second.has_certify_secret)

    def test_choose_key(self):
        bundles = backup.parse_secret_keys(COLONS_TWO_KEYS)
        with self.assertRaises(backup.AmbiguousKey) as ctx:
            backup.choose_key(bundles)
        self.assertEqual(ctx.exception.code, EXIT_BACKUP)
        self.assertEqual(len(ctx.exception.bundles), 2)
        self.assertEqual(backup.choose_key(bundles, "02045ac9d").key_id, "D35238B02045AC9D")
        with self.assertRaises(RecoveryError):
            backup.choose_key(bundles, "FFFF")


class DetectTests(unittest.TestCase):
    def test_detects_each_format(self):
        for kind in ("export", "subkeys-only", "gnupghome"):
            with self.subTest(kind):
                found = backup.detect(FIXTURES / "ed25519" / kind)
                self.assertEqual([c.kind for c in found], [kind])

    def test_parent_folder_needs_a_choice(self):
        found = backup.detect(FIXTURES / "ed25519")
        self.assertEqual([c.kind for c in found], ["export", "gnupghome", "subkeys-only"])
        with self.assertRaises(RecoveryError) as ctx:
            backup.choose(found)
        self.assertEqual(ctx.exception.code, EXIT_BACKUP)
        self.assertEqual(backup.choose(found, "gnupghome").kind, "gnupghome")

    def test_missing_folder(self):
        with self.assertRaises(RecoveryError) as ctx:
            backup.detect(Path("/nonexistent/backup"))
        self.assertEqual(ctx.exception.code, EXIT_BACKUP)


class HomeRegistryTests(unittest.TestCase):
    def test_exit_hook_removes_homes_left_behind(self):
        from ykrecover import gpg
        kept, done = Home.create("test"), Home.create("test")
        self.addCleanup(kept.destroy)
        done.destroy()
        self.assertIn(kept.path, gpg._LIVE_HOMES)
        self.assertNotIn(done.path, gpg._LIVE_HOMES)
        gpg.destroy_all_homes()
        self.assertFalse(kept.path.exists())
        self.assertEqual(gpg._LIVE_HOMES, set())


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.home = Home.create("test")
        self.addCleanup(self.home.destroy)

    def load(self, profile: str, kind: str):
        candidate = backup.choose(backup.detect(FIXTURES / profile / kind))
        return backup.choose_key(backup.load(candidate, self.home))

    def test_export_matches_fixture(self):
        info = fixture_info("ed25519")
        bundle = self.load("ed25519", "export")
        self.assertEqual((bundle.key_fp, bundle.key_id), (info["KEY_FP"], info["KEY_ID"]))
        self.assertTrue(bundle.has_certify_secret)
        self.assertEqual({k.usage: k.fpr for k in bundle.subkeys},
                         {"s": info["FPR_S"], "e": info["FPR_E"], "a": info["FPR_A"]})
        self.assertEqual({k.algo for k in bundle.subkeys}, {"ed25519", "cv25519"})

    def test_rsa_algorithm_names(self):
        self.assertEqual({k.algo for k in self.load("rsa4096", "export").subkeys}, {"rsa4096"})

    def test_subkeys_only_has_no_certify_secret(self):
        self.assertFalse(self.load("ed25519", "subkeys-only").has_certify_secret)

    def test_gnupghome_copy(self):
        self.assertEqual(self.load("ed25519", "gnupghome").key_fp, fixture_info("ed25519")["KEY_FP"])

    def test_passphrase_check(self):
        bundle = self.load("ed25519", "export")
        self.assertFalse(backup.check_passphrase(self.home, bundle, "wrong"))
        self.assertTrue(backup.check_passphrase(self.home, bundle, PASSPHRASE))

    def test_source_is_not_modified(self):
        source = FIXTURES / "ed25519" / "gnupghome"
        before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in source.rglob("*")}
        self.load("ed25519", "gnupghome")
        after = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in source.rglob("*")}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
