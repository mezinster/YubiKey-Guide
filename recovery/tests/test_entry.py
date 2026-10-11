import importlib.util
import io
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest import mock

from tests.helpers import RECOVERY
from ykrecover import termination_signals
from ykrecover import __main__ as entry


class EntryTests(unittest.TestCase):
    def test_refuses_unsupported_platform_with_a_message(self):
        stderr = io.StringIO()
        with mock.patch.object(entry.sys, "platform", "darwin"), redirect_stderr(stderr):
            self.assertEqual(entry.main(), 1)
        self.assertIn("runs on Linux or Windows only", stderr.getvalue())

    def test_ykman_redirect_hands_over_argv(self):
        seen = {}

        def fake_main():
            seen["argv"] = list(entry.sys.argv)
            return 0

        fake_module = SimpleNamespace(main=fake_main)
        with mock.patch.dict("sys.modules", {"ykman": SimpleNamespace(), "ykman._cli": SimpleNamespace(),
                                             "ykman._cli.__main__": fake_module}), \
                mock.patch.object(entry.sys, "argv", ["ykrecover", "--ykman", "list", "--serials"]), \
                mock.patch.object(entry.sys, "platform", "darwin"):
            self.assertEqual(entry.main(), 0)
        self.assertEqual(seen["argv"], ["ykman", "list", "--serials"])

    @unittest.skipUnless(importlib.util.find_spec("ykman"), "yubikey-manager not installed")
    def test_real_ykman_version(self):
        result = subprocess.run([sys.executable, "-m", "ykrecover", "--ykman", "--version"],
                                capture_output=True, text=True, check=False, cwd=RECOVERY)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("YubiKey Manager", result.stdout)

    def test_windowed_exe_refuses_command_line_modes_with_a_dialog(self):
        for flag in ("--cli", "--ykman"):
            with mock.patch.object(entry.sys, "frozen", True, create=True), \
                    mock.patch.object(entry.sys, "stdout", None), \
                    mock.patch.object(entry.sys, "argv", ["ykrecover-gui.exe", flag, "--help"]), \
                    mock.patch.object(entry, "console_only_message") as shown, \
                    mock.patch.object(entry, "run_ykman") as ykman:
                self.assertEqual(entry.main(), 1)
            shown.assert_called_once_with()
            ykman.assert_not_called()

    def test_console_exe_runs_command_line_modes(self):
        with mock.patch.object(entry.sys, "frozen", True, create=True), \
                mock.patch.object(entry.sys, "argv", ["ykrecover.exe", "--ykman", "list"]), \
                mock.patch.object(entry, "console_only_message") as shown, \
                mock.patch.object(entry, "run_ykman", return_value=0) as ykman:
            self.assertEqual(entry.main(), 0)
        shown.assert_not_called()
        ykman.assert_called_once_with(["list"])

    def test_termination_signals_skip_missing_sighup(self):
        windows_like = SimpleNamespace(SIGTERM=15)  # no SIGHUP attribute
        self.assertEqual(termination_signals(windows_like), (15,))
        self.assertEqual(termination_signals(SimpleNamespace(SIGTERM=15, SIGHUP=1)), (15, 1))

    def test_termination_signals_include_sigbreak_on_windows(self):
        windows_like = SimpleNamespace(SIGTERM=15, SIGBREAK=21)  # Ctrl-Break, console closed
        self.assertEqual(termination_signals(windows_like), (15, 21))


if __name__ == "__main__":
    unittest.main()
