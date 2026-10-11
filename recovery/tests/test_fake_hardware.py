import json
import os
import signal
import stat
import unittest
from pathlib import Path

from tests.helpers import (FIXTURES, LINUX, PASSPHRASE, _runtime_base, fake_hardware, fixture_info, read_until,
                           run_impl, short_tempdir, spawn_impl)

S1, S2 = "11111111", "22222222"


def tool_homes():
    return set(_runtime_base().glob("ykr-*"))


class FakeHardwareTests:
    """Full migrations against fake card operations; shared by both implementations."""

    IMPL = ""

    def migrate(self, profile, extra, stdin, serials=(S1,), fail_at="", list_script=None):
        env = fake_hardware(self, list(serials), fail_at, list_script)
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

    def test_token_text_in_a_pin_is_not_substituted(self):
        # Each PIN holds the other's token, so any multi-pass order rewrites one of them.
        admin, user = "<USER_PIN>1", "<ADMIN_PIN>2"
        stdin = f"{PASSPHRASE}\ny\n{admin}\n{admin}\n{user}\n{user}\n\nRESET {S1}\n"
        result, _, _, state = self.migrate("ed25519", ["--pins", "enter"], stdin)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((state / "change-pin.txt").read_text(),
                         f"3\n12345678\n{admin}\n{admin}\nq\n1\n123456\n{user}\n{user}\nq\n")

    def test_retry_after_failure(self):
        stdin = f"{PASSPHRASE}\ny\n\nRESET {S1}\nr\n"
        result, _, calls, _ = self.migrate("ed25519", [], stdin, fail_at="keytocard2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls.count(f"ykman --device {S1} openpgp reset -f"), 2)

    def test_retry_requires_the_same_card(self):
        stdin = f"{PASSPHRASE}\ny\n\nRESET {S1}\nr\n\n"
        result, _, calls, state = self.migrate("ed25519", [], stdin, fail_at="keytocard2",
                                               list_script=(S1, S2, S1))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"card {S1} must be the only YubiKey connected; found {S2}", result.stderr)
        self.assertEqual(calls.count("ykman list --serials"), 3)
        self.assertEqual(calls.count(f"ykman --device {S1} openpgp reset -f"), 2)
        self.assertNotIn(f"--device {S2}", calls)
        info = fixture_info("ed25519")
        self.assertEqual(self.card_fprs(state, S1), [info["FPR_S"], info["FPR_E"], info["FPR_A"]])

    def test_retry_without_the_card_aborts_at_eof(self):
        stdin = f"{PASSPHRASE}\ny\n\nRESET {S1}\nr\n"
        result, _, calls, _ = self.migrate("ed25519", [], stdin, fail_at="keytocard2", list_script=(S1, ""))
        self.assertEqual(result.returncode, 130, result.stderr)
        self.assertIn(f"card {S1} must be the only YubiKey connected; found none", result.stderr)
        self.assertEqual(calls.count(f"ykman --device {S1} openpgp reset -f"), 1)
        self.assertIn(f"Card {S1}: Admin PIN", result.stderr)

    def test_failed_card_listing_shows_ykman_error(self):
        result, _, _, _ = self.migrate("ed25519", [], f"{PASSPHRASE}\ny\n\n\nRESET {S1}\n", fail_at="list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("expected exactly one YubiKey, found 0: fake ykman: cannot reach pcscd", result.stderr)

    def test_out_that_cannot_be_created(self):
        env = fake_hardware(self, [S1])
        blocker = short_tempdir(self, "out") / "file"
        blocker.write_text("not a folder")
        result = run_impl(self.IMPL, ["--backup", str(FIXTURES / "ed25519" / "export"), "--out",
                                      str(blocker / "new")], f"{PASSPHRASE}\ny\n", env)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("cannot create the --out folder", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

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

    @unittest.skipUnless(LINUX, "POSIX signals and select() on pipes")
    def test_termination_signal_at_a_prompt_cleans_up(self):
        for sig in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=sig.name):
                before = tool_homes()
                env = fake_hardware(self, [S1])
                out = short_tempdir(self, "out")
                proc = spawn_impl(self, self.IMPL, ["--backup", str(FIXTURES / "ed25519" / "export"),
                                                    "--out", str(out)], env)
                proc.stdin.write(f"{PASSPHRASE}\ny\n".encode())
                proc.stdin.flush()
                read_until(proc.stderr, "Insert card 1 of 1 and press Enter", timeout=60)
                self.assertNotEqual(tool_homes() - before, set())  # staging exists while waiting
                proc.send_signal(sig)
                self.assertEqual(proc.wait(timeout=15), 130)
                self.assertEqual(tool_homes() - before, set())

    @unittest.skipUnless(LINUX, "POSIX signals and select() on pipes")
    def test_termination_signal_after_a_written_card_shows_pins(self):
        before = tool_homes()
        env = fake_hardware(self, [S1, S2])
        out = short_tempdir(self, "out")
        proc = spawn_impl(self, self.IMPL, ["--backup", str(FIXTURES / "ed25519" / "export"), "--out", str(out),
                                            "--cards", "2"], env)
        proc.stdin.write(f"{PASSPHRASE}\ny\n\nRESET {S1}\n".encode())
        proc.stdin.flush()
        read_until(proc.stderr, "Insert card 2 of 2 and press Enter", timeout=120)
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=15), 130)
        self.assertIn(f"Card {S1}: Admin PIN", proc.stderr.read().decode())
        self.assertEqual(tool_homes() - before, set())

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

    @unittest.skipUnless(LINUX, "POSIX file modes")
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

    def test_workdir_hosts_the_temporary_homes(self):
        workdir = short_tempdir(self, "wd")
        result, _, _, state = self.migrate("ed25519", ["--workdir", str(workdir)],
                                           f"{PASSPHRASE}\ny\n\nRESET {S1}\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        homes = (state / "homes.log").read_text().split()
        self.assertTrue(homes)
        self.assertTrue(all(Path(h).resolve().parent == workdir.resolve() for h in homes), homes)
        self.assertEqual(list(workdir.glob("ykr-*")), [])


class PythonFakeHardwareTests(FakeHardwareTests, unittest.TestCase):
    IMPL = "python"


@unittest.skipUnless(LINUX, "the bash implementation is Linux-only")
class BashFakeHardwareTests(FakeHardwareTests, unittest.TestCase):
    IMPL = "bash"


if __name__ == "__main__":
    unittest.main()
