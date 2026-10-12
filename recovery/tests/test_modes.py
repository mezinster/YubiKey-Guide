import unittest
from pathlib import Path

from ykrecover import card, modes
from ykrecover.backup import Candidate, KeyBundle, Subkey
from ykrecover.migrate import Options, Session

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


NOAUTH = (Subkey("S" * 40, "", "s", "ed25519", 1, 0, "u"), Subkey("E" * 40, "", "e", "cv25519", 1, 0, "u"))


class MissingSlotTests(unittest.TestCase):
    def test_select_targets_marks_missing(self):
        targets = modes.select_targets(bundle(subkeys=NOAUTH))
        self.assertEqual(targets, {"s": "S" * 40, "e": "E" * 40, "a": None})
        self.assertEqual(modes.missing(targets), ["a"])

    def test_renew_adds_missing_subkey(self):
        targets = modes.select_targets(bundle(subkeys=NOAUTH))
        steps = modes.mode_steps("renew", bundle(subkeys=NOAUTH), targets, "2y", "ed25519", False, NOW)
        self.assertEqual(steps[0].argv[-5:], ("--quick-set-expire", "F" * 40, "2y", "S" * 40, "E" * 40))
        self.assertEqual(steps[1].argv[-5:], ("--quick-add-key", "F" * 40, "ed25519", "auth", "2y"))

    def test_renew_without_any_subkey_only_adds(self):
        targets = modes.select_targets(bundle(subkeys=()))
        steps = modes.mode_steps("renew", bundle(subkeys=()), targets, "2y", "ed25519", False, NOW)
        self.assertEqual([s.argv[s.argv.index("--quick-add-key") + 3] for s in steps], ["sign", "encrypt", "auth"])

    def test_card_steps_skip_empty_slot(self):
        targets = {"s": "S" * 40, "e": "E" * 40, "a": None}
        steps = card.card_steps(1, "F" * 40, targets, card.CardSettings())
        keytocard = [s for s in steps if "--edit-key" in s.argv]
        self.assertEqual([s.stdin[2] for s in keytocard], ["1", "2"])
        touch = [s.argv[s.argv.index("set-touch") + 1] for s in steps if "set-touch" in s.argv]
        self.assertEqual(touch, ["sig", "dec"])

    def test_finish_skips_ssh_without_auth(self):
        steps = modes.finish_steps("F" * 40, "F" * 16, {"s": "S" * 40, "e": "E" * 40, "a": None})
        self.assertFalse(any("--export-ssh-key" in s.argv for s in steps))

    def test_default_algo_falls_back_to_main_key(self):
        self.assertEqual(modes.default_algo(bundle(subkeys=(), algo="rsa2048"), modes.select_targets(
            bundle(subkeys=(), algo="rsa2048"))), "rsa4096")


class UnprotectedTests(unittest.TestCase):
    def test_keytocard_without_passphrase_line(self):
        targets = {"s": "S" * 40, "e": "E" * 40, "a": "A" * 40}
        steps = card.card_steps(1, "F" * 40, targets, card.CardSettings(), frozenset(targets.values()))
        keytocard = [s for s in steps if "--edit-key" in s.argv]
        self.assertEqual(keytocard[0].stdin, ("key " + "S" * 40, "keytocard", "1", "<ADMIN_PIN>", "save"))

    def test_passphrase_line_decided_per_subkey(self):
        targets = {"s": "S" * 40, "e": "E" * 40, "a": "A" * 40}
        steps = card.card_steps(1, "F" * 40, targets, card.CardSettings(), frozenset({"E" * 40}))
        stdins = [s.stdin for s in steps if "--edit-key" in s.argv]
        self.assertEqual(stdins, [("key " + "S" * 40, "keytocard", "1", "<CERTIFY_PASS>", "<ADMIN_PIN>", "save"),
                                  ("key " + "E" * 40, "keytocard", "2", "<ADMIN_PIN>", "save"),
                                  ("key " + "A" * 40, "keytocard", "3", "<CERTIFY_PASS>", "<ADMIN_PIN>", "save")])

    def session(self, bare):
        s = Session(Options(backup=Path(".")), None)
        s.candidate = Candidate("export", Path("."), (), "F" * 16, False, bare)
        s.bundle = bundle()
        s.loaded_fprs = frozenset(k.fpr for k in s.bundle.subkeys)
        return s

    def test_bare_subkeys_with_protected_main(self):
        s = self.session(("S" * 16, "E" * 16, "A" * 16))
        targets = {"s": "S" * 40, "e": "E" * 40, "a": "A" * 40}
        self.assertEqual(s.bare_targets(targets), frozenset(targets.values()))
        self.assertEqual(s.bare_targets(modes.ROTATED), frozenset())  # new subkeys follow the main key
        self.assertEqual(s.bare_targets({"s": "S" * 40, "e": None, "a": "<NEW_FPR_A>"}), frozenset({"S" * 40}))

    def test_revoke_follows_main_key_protection(self):
        for bare, has_pass in ((("S" * 16, "E" * 16, "A" * 16), True), (("F" * 16,), False)):
            s = self.session(bare)
            s.opts = Options(backup=Path("."), mode="rotate", revoke_old=True)
            s.targets = modes.select_targets(s.bundle)
            edits = [st for st in s.staging_steps() if "--edit-key" in st.argv]
            self.assertTrue(edits)
            self.assertTrue(all((modes.PASS in st.stdin) == has_pass for st in edits), bare)

    def test_new_subkeys_follow_a_bare_main_key(self):
        s = self.session(("F" * 16,))
        self.assertEqual(s.bare_targets(modes.ROTATED), frozenset(modes.ROTATED.values()))
        self.assertEqual(s.bare_targets({"s": "S" * 40, "e": "E" * 40, "a": "<NEW_FPR_A>"}),
                         frozenset({"<NEW_FPR_A>"}))
        steps = s.card_plan(1, modes.ROTATED)
        self.assertTrue(all("<CERTIFY_PASS>" not in st.stdin for st in steps if "--edit-key" in st.argv))

    def test_revoke_without_passphrase_line(self):
        targets = {"s": "S" * 40, "e": "E" * 40, "a": "A" * 40}
        steps = modes.mode_steps("rotate", bundle(), targets, "2y", "ed25519", True, NOW, unprotected=True)
        edits = [s for s in steps if "--edit-key" in s.argv]
        self.assertTrue(edits and all(modes.PASS not in s.stdin for s in edits))
