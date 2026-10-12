import unittest

from ykrecover import modes
from ykrecover.backup import KeyBundle, Subkey

NOW = 1_800_000_000
DAY = 86400


def bundle(expires=0, subkeys=None, algo="ed25519"):
    subs = subkeys if subkeys is not None else (
        Subkey("S" * 40, "", "s", "ed25519", 1, 0, "u"), Subkey("E" * 40, "", "e", "cv25519", 1, 0, "u"),
        Subkey("A" * 40, "", "a", "ed25519", 1, 0, "u"))
    return KeyBundle("F" * 40, "F" * 16, ("T",), tuple(subs), True, expires, algo)


class ExpireTests(unittest.TestCase):
    def test_expire_at(self):
        self.assertEqual(modes.expire_at("2y", NOW), NOW + 730 * DAY)
        self.assertEqual(modes.expire_at("10", NOW), NOW + 10 * DAY)
        self.assertEqual(modes.expire_at("3w", NOW), NOW + 21 * DAY)
        self.assertEqual(modes.expire_at("1m", NOW), NOW + 30 * DAY)
        self.assertEqual(modes.expire_at("2099-12-31", NOW), 4102358400)
        for never in ("0", "never", "none", "NEVER", "junk", "2023-02-30"):
            self.assertIsNone(modes.expire_at(never, NOW), never)

    def test_main_key_rule(self):
        self.assertFalse(modes.main_needs_extending(bundle(0), "2y", NOW))  # never expires: untouched
        self.assertFalse(modes.main_needs_extending(bundle(0), "never", NOW))
        self.assertTrue(modes.main_needs_extending(bundle(NOW - 1), "2y", NOW))  # expired
        self.assertTrue(modes.main_needs_extending(bundle(NOW + DAY), "2y", NOW))  # before the subkeys
        self.assertFalse(modes.main_needs_extending(bundle(NOW + 1000 * DAY), "2y", NOW))
        self.assertTrue(modes.main_needs_extending(bundle(NOW + 1000 * DAY), "never", NOW))

    def test_renew_extends_main_first(self):
        steps = modes.mode_steps("renew", bundle(NOW - 1), {"s": "S" * 40, "e": "E" * 40, "a": "A" * 40},
                                 "2y", "ed25519", False, NOW)
        self.assertEqual(steps[0].argv[-3:], ("--quick-set-expire", "F" * 40, "2y"))
        self.assertEqual(steps[1].argv[-6:-3], ("--quick-set-expire", "F" * 40, "2y"))
        self.assertEqual(len(steps), 2)

    def test_rotate_extends_main_first(self):
        steps = modes.mode_steps("rotate", bundle(NOW - 1), {"s": "S" * 40, "e": "E" * 40, "a": "A" * 40},
                                 "2y", "ed25519", False, NOW)
        self.assertEqual(steps[0].argv[-3:], ("--quick-set-expire", "F" * 40, "2y"))
        self.assertIn("--quick-add-key", steps[1].argv)
