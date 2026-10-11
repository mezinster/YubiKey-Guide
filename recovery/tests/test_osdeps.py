import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ykrecover import EXIT_USAGE, RecoveryError, osdeps


def win(test):
    """Pretend to be Windows for the duration of a test."""
    patcher = mock.patch.object(osdeps.sys, "platform", "win32")
    patcher.start()
    test.addCleanup(patcher.stop)


class ToolTests(unittest.TestCase):
    def test_linux_tools_are_plain_names(self):
        with mock.patch.object(osdeps.sys, "platform", "linux"):
            for name in ("gpg", "gpgconf", "gpg-connect-agent", "ykman"):
                self.assertEqual(osdeps.tool(name), [name])

    def test_windows_finds_gpg4win_outside_path(self):
        win(self)
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        (root / "GnuPG" / "bin").mkdir(parents=True)
        exe = root / "GnuPG" / "bin" / "gpgconf.exe"
        exe.write_text("")
        env = {"ProgramFiles(x86)": str(root / "missing"), "ProgramFiles": str(root)}
        with mock.patch.dict(os.environ, env), mock.patch.object(osdeps.shutil, "which", return_value=None):
            self.assertEqual(osdeps.tool("gpgconf"), [str(exe)])

    def test_windows_prefers_path(self):
        win(self)
        with mock.patch.object(osdeps.shutil, "which", return_value=r"C:\bin\gpg.exe"):
            self.assertEqual(osdeps.tool("gpg"), [r"C:\bin\gpg.exe"])

    def test_missing_gpg4win_raises_clear_error(self):
        win(self)
        with mock.patch.dict(os.environ, {"ProgramFiles(x86)": "", "ProgramFiles": ""}), \
                mock.patch.object(osdeps.shutil, "which", return_value=None):
            with self.assertRaises(RecoveryError) as ctx:
                osdeps.tool("gpg")
            self.assertEqual(ctx.exception.code, EXIT_USAGE)
            self.assertIn("install Gpg4win", str(ctx.exception))
            self.assertFalse(osdeps.available("gpg"))

    def test_windows_ykman_is_bundled(self):
        win(self)
        with mock.patch.object(osdeps.shutil, "which", return_value=None):
            with mock.patch.object(osdeps.sys, "frozen", True, create=True), \
                    mock.patch.object(osdeps.sys, "executable", r"C:\app\ykrecover-gui.exe"):
                self.assertEqual(osdeps.tool("ykman"), [str(Path(r"C:\app\ykrecover-gui.exe").with_name(
                    "ykrecover.exe")), "--ykman"])
            self.assertEqual(osdeps.tool("ykman"), [sys.executable, "-c", osdeps.YKMAN_SHIM])


class WorkBaseTests(unittest.TestCase):
    def tearDown(self):
        osdeps.set_workdir(None)

    def test_workdir_overrides(self):
        osdeps.set_workdir(Path("/some/where"))
        self.assertEqual(osdeps.work_base(), Path("/some/where"))

    def test_linux_default(self):
        with mock.patch.object(osdeps.sys, "platform", "linux"), \
                mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": ""}):
            self.assertEqual(osdeps.work_base(), Path("/tmp"))

    def test_windows_default_is_created_under_localappdata(self):
        win(self)
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(root)}):
            base = osdeps.work_base()
        self.assertEqual(base, root / "Temp" / "ykrecover")
        self.assertTrue(base.is_dir())


class HomeAndRunTests(unittest.TestCase):
    def test_user_gnupg_home(self):
        with mock.patch.dict(os.environ, {"GNUPGHOME": "/x/gnupg"}):
            self.assertEqual(osdeps.user_gnupg_home(), Path("/x/gnupg"))
        env = {k: v for k, v in os.environ.items() if k != "GNUPGHOME"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(osdeps.sys, "platform", "linux"):
                self.assertEqual(osdeps.user_gnupg_home(), Path.home() / ".gnupg")
            with mock.patch.object(osdeps.sys, "platform", "win32"), \
                    mock.patch.dict(os.environ, {"APPDATA": "/appdata"}):
                self.assertEqual(osdeps.user_gnupg_home(), Path("/appdata") / "gnupg")

    def test_secure_delete_removes_read_only_files(self):
        root = Path(tempfile.mkdtemp())
        (root / "sub").mkdir()
        f = root / "sub" / "secret.key"
        f.write_bytes(b"secret")
        f.chmod(stat.S_IREAD)
        osdeps.secure_delete(root)
        self.assertFalse(root.exists())

    def test_secure_delete_overwrites_on_windows(self):
        win(self)
        root = Path(tempfile.mkdtemp())
        f = root / "secret.key"
        f.write_bytes(b"secret")
        seen = []
        real_rmtree = osdeps.shutil.rmtree

        def spy(path, **kwargs):
            seen.append(f.read_bytes())
            real_rmtree(path, **kwargs)

        with mock.patch.object(osdeps.shutil, "rmtree", spy):
            osdeps.secure_delete(root)
        self.assertEqual(seen, [b"\0" * 6])

    def test_run_decodes_utf8_output(self):
        code = "import sys; sys.stdout.buffer.write('Jürgen Müller\\n'.encode('utf-8'))"
        with mock.patch.object(osdeps, "tool", lambda name: [name]):
            result = osdeps.run([sys.executable, "-c", code])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "Jürgen Müller\n")

    def test_run_resolves_the_tool(self):
        with mock.patch.object(osdeps, "tool", return_value=[sys.executable, "-c"]) as tool:
            result = osdeps.run(["fake", "print('ok')"])
        tool.assert_called_once_with("fake")
        self.assertEqual(result.stdout.strip(), "ok")

    def test_memory_backed_and_network_never_raise(self):
        self.assertIsInstance(osdeps.memory_backed(Path(tempfile.gettempdir())), bool)
        self.assertIsInstance(osdeps.network_up(), list)

    def test_supported(self):
        for platform, expected in (("linux", True), ("win32", True), ("darwin", False)):
            with mock.patch.object(osdeps.sys, "platform", platform):
                self.assertEqual(osdeps.supported(), expected)


if __name__ == "__main__":
    unittest.main()
