import unittest

from ykrecover import RecoveryError, card, modes
from ykrecover.backup import KeyBundle, Subkey
from ykrecover.plan import Step, format_plan, public_tokens, substitute

FP = "A" * 40


def sub(fpr, usage, created, algo="ed25519", expires=0, validity="u"):
    return Subkey(fpr, "G" + fpr[1:], usage, algo, created, expires, validity)


BUNDLE = KeyBundle(FP, "A" * 16, ("T <t@x>",), (
    sub("S1" * 20, "s", 100), sub("E1" * 20, "e", 100, "cv25519"), sub("A1" * 20, "a", 100),
    sub("S2" * 20, "s", 200), sub("S3" * 20, "s", 300, validity="r"),
), True)


class PlanTests(unittest.TestCase):
    def test_format_plan(self):
        steps = [Step("mode", "<STAGING>", ("gpg", "--x", "y"), ("<CERTIFY_PASS>", "")),
                 Step("verify1", "<VERIFYHOME>", ("gpg", "--card-status"), check="cardstatus")]
        self.assertEqual(format_plan(steps),
                         "STEP 1 mode <STAGING> gpg --x y\nSTDIN <CERTIFY_PASS>\nSTDIN \n"
                         "STEP 2 verify1 <VERIFYHOME> gpg --card-status\nCHECK cardstatus\n")

    def test_substitute_and_redact(self):
        tokens = {"<ADMIN_PIN>": "87654321", "<OUT>": "/out"}
        self.assertEqual(substitute("-a <ADMIN_PIN> <OUT>/x", tokens), "-a 87654321 /out/x")
        self.assertEqual(substitute("-a <ADMIN_PIN>", public_tokens(tokens)), "-a <ADMIN_PIN>")


class ModeTests(unittest.TestCase):
    def test_select_targets_newest_unrevoked(self):
        self.assertEqual(modes.select_targets(BUNDLE), {"s": "S2" * 20, "e": "E1" * 20, "a": "A1" * 20})

    def test_missing_usage(self):
        bundle = KeyBundle(FP, "A" * 16, (), (sub("S1" * 20, "s", 1),), True)
        with self.assertRaises(RecoveryError):
            modes.select_targets(bundle)

    def test_expired(self):
        bundle = KeyBundle(FP, "A" * 16, (), (sub("S1" * 20, "s", 1, expires=50),
                                              sub("E1" * 20, "e", 1), sub("A1" * 20, "a", 1, expires=500)), True)
        targets = modes.select_targets(bundle)
        self.assertEqual(modes.expired(bundle, targets, now=100), ["S1" * 20])

    def test_default_algo(self):
        targets = modes.select_targets(BUNDLE)
        self.assertEqual(modes.default_algo(BUNDLE, targets), "ed25519")

    def test_clone_has_no_staging_steps(self):
        targets = modes.select_targets(BUNDLE)
        self.assertEqual(modes.mode_steps("clone", BUNDLE, targets, "2y", "ed25519", False), [])
        self.assertEqual(modes.backup_steps("clone", BUNDLE), [])

    def test_renew(self):
        targets = modes.select_targets(BUNDLE)
        (step,) = modes.mode_steps("renew", BUNDLE, targets, "3y", "ed25519", False)
        self.assertEqual(step.argv[step.argv.index("--quick-set-expire") + 1:],
                         (FP, "3y", "S2" * 20, "E1" * 20, "A1" * 20))
        self.assertEqual(step.stdin, ("<CERTIFY_PASS>",))

    def test_rotate_with_revoke(self):
        targets = modes.select_targets(BUNDLE)
        steps = modes.mode_steps("rotate", BUNDLE, targets, "2y", "ed25519", True)
        adds = [s.argv[-3:] for s in steps if "--quick-add-key" in s.argv]
        self.assertEqual(adds, [("ed25519", "sign", "2y"), ("cv25519", "encrypt", "2y"), ("ed25519", "auth", "2y")])
        revokes = [s for s in steps if "--edit-key" in s.argv]
        self.assertEqual([s.stdin[0] for s in revokes], [f"key {targets[u]}" for u in "sea"])
        self.assertEqual(revokes[0].stdin, (f"key {'S2' * 20}", "revkey", "y", "0", "", "y", "<CERTIFY_PASS>", "save"))
        for i, step in enumerate(steps):
            if "--edit-key" in step.argv:
                self.assertEqual(steps[i - 1].argv, ("gpgconf", "--kill", "all"))

    def test_backup_steps(self):
        steps = modes.backup_steps("renew", BUNDLE)
        outputs = [s.argv[s.argv.index("--output") + 1] for s in steps]
        kid = "A" * 16
        self.assertEqual(outputs, [f"<OUT>/secret-{kid}-Certify.key", f"<OUT>/secret-{kid}-Subkeys.key",
                                   f"<OUT>/public-{kid}-<DATE>.asc"])


class CardTests(unittest.TestCase):
    TARGETS = {"s": "S" * 40, "e": "E" * 40, "a": "T" * 40}

    def test_every_card_session_starts_after_kill(self):
        steps = card.card_steps(1, FP, self.TARGETS, card.CardSettings()) + card.verify_steps(1, FP, self.TARGETS)
        for i, step in enumerate(steps):
            scripted = step.argv[0] == "ykman" or any(
                flag in step.argv for flag in ("--change-pin", "--edit-card", "--edit-key"))
            if scripted:
                with self.subTest(step=" ".join(step.argv)):
                    self.assertEqual(steps[i - 1].argv, ("gpgconf", "--kill", "all"))
                    self.assertEqual(steps[i - 1].home, step.home)
            if step.argv[0] == "gpg":
                self.assertIn("--batch", step.argv)

    def test_keytocard_scripts(self):
        steps = [s for s in card.card_steps(2, FP, self.TARGETS, card.CardSettings()) if "--edit-key" in s.argv]
        self.assertEqual([s.stdin for s in steps], [
            (f"key {self.TARGETS[u]}", "keytocard", slot, "<CERTIFY_PASS>", "<ADMIN_PIN>", "save")
            for u, slot in (("s", "1"), ("e", "2"), ("a", "3"))])

    def test_serial_token_and_settings(self):
        settings = card.CardSettings(retries=("3", "3", "3"), touch={"sig": "cached", "dec": "on", "aut": "off"},
                                     name="Jane Q Doe", url="https://example.org/k.asc")
        steps = card.card_steps(2, FP, self.TARGETS, settings)
        text = format_plan(steps)
        self.assertIn("ykman --device <SERIAL2> openpgp reset -f", text)
        self.assertIn("set-retries 3 3 3 -f -a <ADMIN_PIN>", text)
        self.assertIn("set-touch sig cached -f -a <ADMIN_PIN>", text)
        self.assertIn("set-touch aut off -f -a <ADMIN_PIN>", text)
        holder = next(s for s in steps if "--edit-card" in s.argv)
        self.assertEqual(holder.stdin, ("admin", "login", "<LOGIN>", "<ADMIN_PIN>", "name", "<SURNAME>",
                                        "<GIVEN>", "url", "<URL>", "quit"))

    def test_minimal_cardholder(self):
        holder = next(s for s in card.card_steps(1, FP, self.TARGETS, card.CardSettings()) if "--edit-card" in s.argv)
        self.assertEqual(holder.stdin, ("admin", "login", "<LOGIN>", "<ADMIN_PIN>", "quit"))

    def test_verify_checks(self):
        steps = card.verify_steps(1, FP, self.TARGETS)
        self.assertEqual([s.check for s in steps if s.check], ["cardstatus", "roundtrip"])
        self.assertIn(f"{'S' * 40}!", steps[3].argv)

    def test_pins_and_login(self):
        admin, user = card.generate_pins()
        self.assertRegex(admin, r"^\d{8}$")
        self.assertRegex(user, r"^\d{6}$")
        self.assertRegex(card.random_login(), r"^yk\.[a-z0-9]{16}$")

    def test_split_name(self):
        self.assertEqual(card.split_name("Jane Q Doe"), ("Doe", "Jane Q"))
        self.assertEqual(card.split_name("Doe"), ("Doe", ""))
        self.assertEqual(card.split_name(""), ("", ""))

    def test_parsers(self):
        self.assertEqual(card.parse_serials("12345678\n\nnoise\n"), ["12345678"])
        self.assertEqual(card.parse_card_fprs("serial:1:\nfpr:aa:bb:cc:\n"), ["AA", "BB", "CC"])
        self.assertEqual(card.parse_card_fprs("serial:1:\n"), ["", "", ""])


if __name__ == "__main__":
    unittest.main()
