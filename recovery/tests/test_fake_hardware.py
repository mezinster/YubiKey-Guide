import json
import os
import stat
import unittest
from pathlib import Path

from tests.helpers import FIXTURES, PASSPHRASE, _runtime_base, fake_hardware, fixture_info, run_impl, short_tempdir

S1, S2 = "11111111", "22222222"


def tool_homes():
    return set(_runtime_base().glob("ykr-*"))


class FakeHardwareTests:
    """Full migrations against fake card operations; shared by both implementations."""

    IMPL = ""

    def migrate(self, profile, extra, stdin, serials=(S1,), fail_at=""):
        env = fake_hardware(self, list(serials), fail_at)
        out = short_tempdir(self, "out")
        result = run_impl(self.IMPL, ["--backup", str(FIXTURES / profile / "export"), "--out", str(out), *extra],
                          stdin, env)
        state = Path(env["FAKE_HW_DIR"])
        calls = (state / "calls.log").read_text() if (state / "calls.log").exists() else ""
        return result, out, calls, state

    def card_fprs(self, state, serial):
        return json.loads((state / f"card-{serial}.json").read_text())["fpr"]

    def test_clone_one_card(self):
        info = fixture_info("ed25519")
        result, out, calls, state = self.migrate("ed25519", [], f"{PASSPHRASE}\ny\n\nRESET {S1}\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.card_fprs(state, S1), [info["FPR_S"], info["FPR_E"], info["FPR_A"]])
        self.assertIn(f"ykman --device {S1} openpgp reset -f", calls)
        self.assertIn(f"ykman --device {S1} openpgp keys set-touch aut on -f -a <pin>", calls)
        self.assertIn("decrypt", calls)
        self.assertTrue((out / f"ssh-{info['KEY_ID']}.pub").is_file())
        self.assertNotIn(PASSPHRASE, result.stderr)
        self.assertIn("login\nyk.", (state / "edit-card.txt").read_text())
        self.assertEqual(result.stderr.count("Touch the YubiKey when it blinks"), 2)

    def test_no_touch_prompt_when_touch_off(self):
        result, _, _, _ = self.migrate("ed25519", ["--touch-sig", "off"], f"{PASSPHRASE}\ny\n\nRESET {S1}\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.count("Touch the YubiKey when it blinks"), 1)  # decrypt only
        result, _, _, _ = self.migrate("ed25519", ["--touch", "off"], f"{PASSPHRASE}\ny\n\nRESET {S1}\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Touch the YubiKey", result.stderr)

    def test_two_cards_same_pins(self):
        stdin = f"{PASSPHRASE}\ny\n\nRESET {S1}\n\nRESET {S2}\n"
        result, _, _, state = self.migrate("ed25519", ["--cards", "2", "--same-pins"], stdin, serials=(S1, S2))
        self.assertEqual(result.returncode, 0, result.stderr)
        scripts = (state / "change-pin.txt").read_text().split("q\n")
        admin_pins = {s.split("\n")[2] for s in scripts if s.startswith("3\n")}
        self.assertEqual(len(admin_pins), 1)
        self.assertRegex(admin_pins.pop(), r"^\d{8}$")

    def test_entered_pins_reprompt_when_short(self):
        stdin = f"{PASSPHRASE}\ny\n123\n11223344\n11223344\n654321\n654321\n\nRESET {S1}\n"
        result, _, _, state = self.migrate("ed25519", ["--pins", "enter"], stdin)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((state / "change-pin.txt").read_text(),
                         "3\n12345678\n11223344\n11223344\nq\n1\n123456\n654321\n654321\nq\n")

    def test_retry_after_failure(self):
        stdin = f"{PASSPHRASE}\ny\n\nRESET {S1}\nr\n"
        result, _, calls, _ = self.migrate("ed25519", [], stdin, fail_at="keytocard2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls.count(f"ykman --device {S1} openpgp reset -f"), 2)

    def test_skip_after_failure(self):
        result, _, _, _ = self.migrate("ed25519", [], f"{PASSPHRASE}\ny\n\nRESET {S1}\ns\n", fail_at="sign")
        self.assertEqual(result.returncode, 3)
        self.assertIn("Card 11111111: Admin PIN", result.stderr)
        self.assertIn("FAILED", result.stderr)

    def test_abort_after_written_card_still_shows_pins(self):
        stdin = f"{PASSPHRASE}\ny\n\nRESET {S1}\n\n"
        result, _, _, _ = self.migrate("ed25519", ["--cards", "2"], stdin, serials=(S1, S2))
        self.assertEqual(result.returncode, 130)
        self.assertIn("Card 11111111: Admin PIN", result.stderr)

    def test_abort_mid_card_cleans_up_homes(self):
        before = tool_homes()
        result, _, _, _ = self.migrate("ed25519", [], f"{PASSPHRASE}\ny\n\nRESET {S1}\na\n", fail_at="keytocard1")
        self.assertEqual(result.returncode, 130)
        self.assertEqual(tool_homes() - before, set())
        self.assertIn("Card 11111111: Admin PIN", result.stderr)

    def test_reset_not_confirmed(self):
        result, _, calls, _ = self.migrate("ed25519", [], f"{PASSPHRASE}\ny\n\nreset please\n")
        self.assertEqual(result.returncode, 3)
        self.assertNotIn("reset -f", calls)

    def test_wrong_passphrase_three_times(self):
        result, _, _, _ = self.migrate("ed25519", [], "a\nb\nc\n")
        self.assertEqual(result.returncode, 2)

    def test_declined_key_confirmation(self):
        result, _, calls, _ = self.migrate("ed25519", [], f"{PASSPHRASE}\nn\n")
        self.assertEqual(result.returncode, 130)
        self.assertEqual(calls, "")

    def test_no_card_reprompts_then_eof_aborts(self):
        result, _, _, _ = self.migrate("ed25519", [], f"{PASSPHRASE}\ny\n\n", serials=())
        self.assertEqual(result.returncode, 130)
        self.assertIn("expected exactly one YubiKey, found 0", result.stderr)

    def test_abort_cleans_up_homes(self):
        before = tool_homes()
        result, _, _, _ = self.migrate("ed25519", [], f"{PASSPHRASE}\ny\n\n")
        self.assertEqual(result.returncode, 130)
        self.assertEqual(tool_homes() - before, set())

    def test_renew_requires_saved_backup_confirmation(self):
        info = fixture_info("expired")
        stdin = f"{PASSPHRASE}\ny\nn\ny\n\nRESET {S1}\n"
        result, out, _, state = self.migrate("expired", ["--mode", "renew"], stdin)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("copy the new backup before writing cards", result.stderr)
        self.assertTrue((out / f"secret-{info['KEY_ID']}-Certify.key").is_file())
        self.assertEqual(self.card_fprs(state, S1), [info["FPR_S"], info["FPR_E"], info["FPR_A"]])

    def test_outputs_are_owner_only(self):
        info = fixture_info("ed25519")
        previous = os.umask(0o022)  # gpg writes these files with the process umask
        self.addCleanup(os.umask, previous)
        result, out, _, _ = self.migrate("ed25519", [], f"{PASSPHRASE}\ny\n\nRESET {S1}\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        files = list(out.glob("public-*.asc")) + [out / f"ssh-{info['KEY_ID']}.pub"]
        self.assertEqual(len(files), 2)
        for f in files:
            self.assertEqual(stat.S_IMODE(os.stat(f).st_mode) & 0o077, 0, f.name)

    def test_rotate_writes_new_subkeys(self):
        info = fixture_info("rsa4096")
        stdin = f"{PASSPHRASE}\ny\ny\n\nRESET {S1}\n"
        result, _, _, state = self.migrate("rsa4096", ["--mode", "rotate", "--algo", "ed25519", "--revoke-old"], stdin)
        self.assertEqual(result.returncode, 0, result.stderr)
        fprs = self.card_fprs(state, S1)
        self.assertTrue(all(fprs))
        self.assertFalse(set(fprs) & {info["FPR_S"], info["FPR_E"], info["FPR_A"]})


class PythonFakeHardwareTests(FakeHardwareTests, unittest.TestCase):
    IMPL = "python"


class BashFakeHardwareTests(FakeHardwareTests, unittest.TestCase):
    IMPL = "bash"


if __name__ == "__main__":
    unittest.main()
