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
        with mock.patch.dict(os.environ, env), mock.patch.object(osdeps, "_which", return_value=None):
            self.assertEqual(osdeps.tool("gpgconf"), [str(exe)])

    def test_windows_prefers_path(self):
        win(self)
        with mock.patch.object(osdeps, "_which", return_value=r"C:\bin\gpg.exe"):
            self.assertEqual(osdeps.tool("gpg"), [r"C:\bin\gpg.exe"])

    def test_missing_gpg4win_raises_clear_error(self):
        win(self)
        with mock.patch.dict(os.environ, {"ProgramFiles(x86)": "", "ProgramFiles": ""}), \
                mock.patch.object(osdeps, "_which", return_value=None):
            with self.assertRaises(RecoveryError) as ctx:
                osdeps.tool("gpg")
            self.assertEqual(ctx.exception.code, EXIT_USAGE)
            self.assertIn("install Gpg4win", str(ctx.exception))
            self.assertFalse(osdeps.available("gpg"))

    def test_windows_ykman_is_bundled(self):
        win(self)
        app = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(app, ignore_errors=True))
        (app / "ykrecover.exe").write_text("")
        with mock.patch.object(osdeps, "_which", return_value=None):
            with mock.patch.object(osdeps.sys, "frozen", True, create=True), \
                    mock.patch.object(osdeps.sys, "executable", str(app / "ykrecover-gui.exe")):
                self.assertEqual(osdeps.tool("ykman"), [str(app / "ykrecover.exe"), "--ykman"])
                self.assertTrue(osdeps.available("ykman"))
            self.assertEqual(osdeps.tool("ykman"), [sys.executable, "-c", osdeps.YKMAN_SHIM])

    def test_frozen_gui_without_console_exe_fails_preflight(self):
        win(self)
        app = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(app, ignore_errors=True))
        with mock.patch.object(osdeps, "_which", return_value=None), \
                mock.patch.object(osdeps.sys, "frozen", True, create=True), \
                mock.patch.object(osdeps.sys, "executable", str(app / "ykrecover-gui.exe")):
            with self.assertRaises(RecoveryError) as ctx:
                osdeps.tool("ykman")
            self.assertEqual(ctx.exception.code, EXIT_USAGE)
            self.assertEqual(str(ctx.exception),
                             "ykrecover.exe must be next to ykrecover-gui.exe: extract the whole zip")
            self.assertFalse(osdeps.available("ykman"))

    def test_planted_exe_in_cwd_is_ignored(self):
        win(self)
        cwd = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(cwd, ignore_errors=True))
        (cwd / "gpg.exe").write_text("")
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(cwd)
        for path in ("", ".", "relative"):
            with mock.patch.dict(os.environ, {"PATH": path, "ProgramFiles(x86)": "", "ProgramFiles": ""}):
                with self.assertRaises(RecoveryError):
                    osdeps.tool("gpg")

    def test_cmd_in_absolute_path_dir_is_found(self):
        win(self)
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        (d / "gpg.cmd").write_text("")
        with mock.patch.dict(os.environ, {"PATH": "relative" + os.pathsep + str(d),
                                          "PATHEXT": ".COM;.EXE;.BAT;.CMD"}):
            found = osdeps.tool("gpg")
        self.assertEqual(len(found), 1)
        self.assertEqual(os.path.normcase(found[0]), os.path.normcase(str(d / "gpg.cmd")))

    def test_windows_unknown_tool_raises(self):
        win(self)
        with mock.patch.object(osdeps, "_which", return_value=None):
            with self.assertRaises(RecoveryError) as ctx:
                osdeps.tool("nonesuch")
        self.assertEqual(ctx.exception.code, EXIT_USAGE)


class WorkBaseTests(unittest.TestCase):
    def tearDown(self):
        osdeps.set_workdir(None)

    def test_workdir_overrides(self):
        osdeps.set_workdir(Path("/some/where"))
        self.assertEqual(osdeps.work_base(), Path("/some/where"))

    def test_default_work_base_ignores_the_override(self):
        with mock.patch.object(osdeps.sys, "platform", "linux"), \
                mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": ""}):
            osdeps.set_workdir(Path("/some/where"))
            self.assertEqual(osdeps.default_work_base(), Path("/tmp"))
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
    def test_user_gnupg_home_asks_gpgconf(self):
        listed = osdeps.subprocess.CompletedProcess([], 0, "C%3a\\Users\\me\\gnupg\n", "")
        with mock.patch.object(osdeps, "run", return_value=listed) as run:
            self.assertEqual(osdeps.user_gnupg_home(), Path("C:\\Users\\me\\gnupg"))
        run.assert_called_once_with(["gpgconf", "--list-dirs", "homedir"])

    def test_user_gnupg_home_honours_gnupghome_through_gpgconf(self):
        if osdeps.is_windows() or not __import__("shutil").which("gpgconf"):
            self.skipTest("needs gpgconf on Linux")
        with mock.patch.dict(os.environ, {"GNUPGHOME": "/x/gnupg"}):
            self.assertEqual(osdeps.user_gnupg_home(), Path("/x/gnupg"))

    def test_user_gnupg_home_without_gpgconf(self):
        failed = osdeps.subprocess.CompletedProcess([], 2, "", "")
        for outcome in ({"return_value": failed}, {"side_effect": FileNotFoundError("gpgconf")},
                        {"side_effect": RecoveryError("gpgconf not found", EXIT_USAGE)}):
            with self.subTest(**{k: repr(v) for k, v in outcome.items()}), \
                    mock.patch.object(osdeps, "run", **outcome):
                self.check_fallback_home()

    def check_fallback_home(self):
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

    def test_secure_delete_result(self):
        root = Path(tempfile.mkdtemp())
        self.assertTrue(osdeps.secure_delete(root))
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        with mock.patch.object(osdeps.shutil, "rmtree", lambda *a, **k: None):
            self.assertFalse(osdeps.secure_delete(root))

    def test_run_sends_input_bytes_without_crlf(self):
        code = "import sys; sys.stdout.write(repr(sys.stdin.buffer.read()))"
        with mock.patch.object(osdeps, "tool", lambda name: [name]):
            result = osdeps.run([sys.executable, "-c", code], input="a\nb\n")
        self.assertEqual(result.stdout, "b'a\\nb\\n'")

    def test_run_without_input_gets_eof(self):
        code = "import sys; sys.stdout.write(repr(sys.stdin.buffer.read()))"
        with mock.patch.object(osdeps, "tool", lambda name: [name]):
            result = osdeps.run([sys.executable, "-c", code])
        self.assertEqual(result.stdout, "b''")

    def test_run_passes_creationflags_on_windows(self):
        win(self)
        done = osdeps.subprocess.CompletedProcess([], 0, b"x\r\n", b"")
        with mock.patch.object(osdeps, "tool", lambda name: [name]), \
                mock.patch.object(osdeps.subprocess, "run", return_value=done) as runner:
            result = osdeps.run(["t"])
        self.assertIn("creationflags", runner.call_args.kwargs)
        self.assertEqual(result.stdout, "x\n")

    def test_memory_backed_windows_drive_types(self):
        win(self)
        for drive_type, expected in ((6, True), (3, False)):
            fake = mock.Mock()
            fake.windll.kernel32.GetDriveTypeW.return_value = drive_type
            with mock.patch.object(osdeps, "ctypes", fake):
                self.assertEqual(osdeps.memory_backed(Path(tempfile.gettempdir())), expected)

    def test_memory_backed_linux_matches_on_path_boundary(self):
        mounts = "tmpfs /tmp tmpfs rw 0 0\n/dev/sda1 / ext4 rw 0 0\n"
        with mock.patch.object(osdeps.sys, "platform", "linux"), \
                mock.patch("builtins.open", mock.mock_open(read_data=mounts)):
            self.assertTrue(osdeps.memory_backed("/tmp/x"))
            self.assertFalse(osdeps.memory_backed("/tmpfoo/x"))

    def test_network_up_windows(self):
        win(self)
        for address, expected in (("10.0.0.5", ["with address 10.0.0.5"]), ("127.0.0.1", [])):
            probe = mock.Mock()
            probe.getsockname.return_value = (address, 1234)
            with mock.patch.object(osdeps.socket, "socket", return_value=probe):
                self.assertEqual(osdeps.network_up(), expected)

    def test_supported(self):
        for platform, expected in (("linux", True), ("win32", True), ("darwin", False)):
            with mock.patch.object(osdeps.sys, "platform", platform):
                self.assertEqual(osdeps.supported(), expected)


if __name__ == "__main__":
    unittest.main()
