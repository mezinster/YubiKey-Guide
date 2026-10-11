import io
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest import mock

from ykrecover import termination_signals
from ykrecover import __main__ as entry


class EntryTests(unittest.TestCase):
    def test_refuses_non_linux_with_a_message(self):
        stderr = io.StringIO()
        with mock.patch.object(entry.sys, "platform", "win32"), redirect_stderr(stderr):
            self.assertEqual(entry.main(), 1)
        self.assertIn("runs on Linux only", stderr.getvalue())

    def test_termination_signals_skip_missing_sighup(self):
        windows_like = SimpleNamespace(SIGTERM=15)  # no SIGHUP attribute
        self.assertEqual(termination_signals(windows_like), (15,))
        self.assertEqual(termination_signals(SimpleNamespace(SIGTERM=15, SIGHUP=1)), (15, 1))


if __name__ == "__main__":
    unittest.main()
