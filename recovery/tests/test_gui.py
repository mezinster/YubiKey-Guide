import os
import signal
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.helpers import FIXTURES, LINUX, PASSPHRASE, fixture_info

try:
    import tkinter as tk
except ImportError:  # python3-tk not installed
    tk = None


NO_GUI = tk is None or (LINUX and not os.environ.get("DISPLAY"))
# CI sets this so a missing python3-tk or display fails the run instead of silently skipping the GUI tests.
REQUIRE_GUI = os.environ.get("YKRECOVER_REQUIRE_GUI") == "1"
SKIP_GUI = unittest.skipIf(NO_GUI and not REQUIRE_GUI, "needs tkinter and a display")


def shown_pins():
    """Patches the PIN dialog's run(); call_args.args[0] is the dialog, its text_content what it shows."""
    from ykrecover.gui import TextDialog
    return mock.patch.object(TextDialog, "run", autospec=True, return_value=True)


class GuiAvailable(unittest.TestCase):
    @unittest.skipUnless(REQUIRE_GUI, "YKRECOVER_REQUIRE_GUI is not set")
    def test_gui_tests_can_run(self):
        self.assertFalse(NO_GUI, "YKRECOVER_REQUIRE_GUI=1, but tkinter or the display is missing")


def detected(app, folder, candidates):
    """Puts candidates on the backup page as if Detect had found them in folder."""
    from ykrecover.backup import Inventory
    app.v["folder"].set(str(folder))
    app.inventory = Inventory(Path(folder), (), tuple(candidates), ())
    app.candidates = list(candidates)
    for c in candidates:
        app.cand_list.insert("end", f"{c.kind}: {c.path}")
    app.cand_list.selection_set(0)


def grab_holder(root):
    """The Tcl name of the window holding the grab, or "". Asks Tcl directly: tkinter's grab_current()
    raises KeyError for Tk's own message boxes (".__tk__messagebox" on X11)."""
    return str(root.tk.call("grab", "current", root._w))


def when_dialog_up(root, action, timeout=5000):
    """Calls action(dialog) from the event loop once a dialog holds the grab. dialog is the Toplevel, or
    the Tcl name of one of Tk's own message boxes."""
    pending = {}

    def later(ms, fn):
        pending[fn.__name__] = root.after(ms, fn)

    def check():
        name = grab_holder(root)
        if not name:
            later(20, check)
            return
        pending.pop("check", None)
        try:
            dialog = root.nametowidget(name)
        except KeyError:  # not created by tkinter
            dialog = name
        action(dialog)

    def unblock():  # a failing test must not hang in a dialog nobody answers
        pending.clear()
        try:
            for name in root.tk.splitlist(root.tk.call("winfo", "children", root._w)):
                if str(root.tk.call("winfo", "toplevel", name)) == str(name):
                    root.tk.call("destroy", name)  # also message boxes, which winfo_children() skips
        except tk.TclError:  # the root is gone
            pass

    def forget(event):  # timers outlive a destroyed root and would fire in a later test's event loop
        if event.widget is root:
            for after_id in pending.values():
                root.after_cancel(after_id)
            pending.clear()

    root.bind("<Destroy>", forget, add="+")
    later(timeout, unblock)  # armed now, so a dialog that never takes the grab cannot hang either
    later(20, check)


@SKIP_GUI
class GuiSmokeTests(unittest.TestCase):
    def test_work_folder_is_applied_before_loading(self):
        from pathlib import Path
        from ykrecover import osdeps
        from ykrecover.gui import App
        from tests.helpers import FIXTURES, PASSPHRASE, short_tempdir
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = App(root)
        app.alert = lambda text: None
        self.addCleanup(lambda: app.session and app.session.cleanup())
        self.addCleanup(osdeps.set_workdir, None)
        workdir = short_tempdir(self, "wd")
        leftover = workdir / "ykr-staging-old"
        leftover.mkdir()
        os.utime(leftover, (1, 1))  # older than a run starting now
        app.v["folder"].set(str(FIXTURES / "ed25519" / "export"))
        app.v["workdir"].set(str(workdir))
        app.v["passphrase"].set(PASSPHRASE)
        app.show(1)
        root.update()
        with shown_pins() as shown:  # the leftover warning uses the same text dialog
            self.assertTrue(app.leave_backup())
        self.assertEqual(Path(app.session.staging.path).parent, workdir)
        self.assertIn(str(workdir), app.workdir_note.cget("text"))
        self.assertIn(str(leftover), shown.call_args.args[0].text_content)

    def test_note_for_a_chosen_disk_work_folder(self):
        import tempfile
        from ykrecover import osdeps
        from ykrecover.gui import App
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = App(root)
        app.show(1)
        folder = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, folder)
        with mock.patch.object(osdeps, "memory_backed", lambda path: False):
            app.v["workdir"].set(folder)
            self.assertEqual(app.workdir_note.cget("text"),
                             f"Work folder {Path(folder)} is not a RAM disk; make sure it is an encrypted volume.")
            app.v["workdir"].set("")
            self.assertIn("which is on disk", app.workdir_note.cget("text"))

    def test_note_ignores_a_stale_override_when_the_field_is_empty(self):
        from pathlib import Path
        from ykrecover import osdeps
        from ykrecover.gui import App
        from tests.helpers import short_tempdir
        root = tk.Tk()
        self.addCleanup(root.destroy)
        self.addCleanup(osdeps.set_workdir, None)
        app = App(root)
        app.show(1)
        osdeps.set_workdir(short_tempdir(self, "stale"))
        app.v["workdir"].set("")
        app.update_workdir_note()
        self.assertIn(str(osdeps.default_work_base()), app.workdir_note.cget("text"))
        self.assertNotIn("stale", app.workdir_note.cget("text"))

    def test_invalid_work_folder_alerts_without_staging(self):
        from ykrecover.backup import Candidate
        from ykrecover.gui import App
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = App(root)
        alerts = []
        app.alert = alerts.append
        app.show(1)
        detected(app, "/backup", [Candidate("export", Path("/backup"), ())])
        app.v["workdir"].set("/nonexistent/work/folder")
        self.assertFalse(app.leave_backup())
        self.assertEqual(len(alerts), 1)
        self.assertIsNone(app.session)

    def test_poll_stops_after_terminate_and_survives_a_failing_one(self):
        app = self.app_with_pins()
        app.request_terminate(signal.SIGTERM, None)
        with shown_pins():
            app.poll()
        self.assertIsNone(app.poll_id)
        app = self.app_with_pins()
        app.request_terminate(signal.SIGTERM, None)
        with mock.patch.object(app, "terminate", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                app.poll()
        self.assertIsNotNone(app.poll_id)

    def test_wizard_builds_and_closes(self):
        from ykrecover.gui import App
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = App(root)
        app.alert = lambda text: None  # keep the test headless-safe: no modal error dialog
        root.update()
        self.assertEqual(app.page, 0)
        self.assertEqual(len(app.pages), 8)
        app.v["folder"].set("/nonexistent")
        app.show(1)
        root.update()
        self.assertFalse(app.leave_backup())  # nothing detected → stays on the page

    def app_with_pins(self):
        from ykrecover.gui import App
        root = tk.Tk()

        def destroy():
            try:
                root.destroy()
            except tk.TclError:  # the app already closed it
                pass

        self.addCleanup(destroy)
        app = App(root)
        app.alert = lambda text: None  # an unexpected alert fails the test instead of waiting for a click
        app.logbox = tk.Text(root)
        app.keep_pins([("11111111", "12345678", "123456")])
        return app

    def test_pins_shown_when_work_finishes(self):
        app = self.app_with_pins()
        with shown_pins() as shown:
            app.finished(0)
            app.reveal_pins()  # once only
        self.assertEqual(shown.call_count, 1)
        self.assertIn("Admin PIN 12345678", shown.call_args.args[0].text_content)
        self.assertEqual(app.pins, [])
        app.session = SimpleNamespace(opts=SimpleNamespace(out="/out", cards=2), results=[])
        app.show(7)
        self.assertFalse(hasattr(app, "pin_btn"))
        summary = app.summary.get("1.0", "end")
        from ykrecover.migrate import update_stubs_command
        self.assertIn(f"{update_stubs_command()} --import <public key file> --remove-serial <old serial> --cards 2",
                      summary)

    def test_callback_errors_are_logged_and_shown(self):
        import io
        from contextlib import redirect_stderr
        app = self.app_with_pins()
        try:
            raise ValueError("boom")
        except ValueError as err:
            error = err
        with mock.patch("ykrecover.gui.messagebox.showerror") as shown, redirect_stderr(io.StringIO()) as err:
            app.report_error(ValueError, error, error.__traceback__)
        self.assertIn("ValueError: boom", shown.call_args.args[1])
        self.assertIn("error: ValueError: boom", app.logbox.get("1.0", "end"))
        self.assertIn("Traceback", err.getvalue())

    def test_close_shows_pins_never_shown(self):
        app = self.app_with_pins()
        with shown_pins() as shown:
            app.close()
        self.assertEqual(shown.call_count, 1)
        self.assertIn("User PIN 123456", shown.call_args.args[0].text_content)

    def test_close_is_refused_while_a_dialog_is_up(self):
        """The window manager's close button is not blocked by a grab: leave_backup() must not go on
        loading the key after the window was closed under the leftover warning."""
        from ykrecover.migrate import Session
        app = self.backup_page_with_leftovers()
        seen = []

        def close_then_answer(dialog):
            app.root.tk.call(app.root.protocol("WM_DELETE_WINDOW"))
            seen.append((dialog.title(), app.root.winfo_exists(), dialog.winfo_exists()))
            dialog.done(None)

        when_dialog_up(app.root, close_then_answer)
        with mock.patch.object(Session, "load") as load, mock.patch.object(Session, "unlock", return_value=True):
            self.assertTrue(app.leave_backup())
        self.assertEqual(seen, [("Work folder", 1, 1)])
        load.assert_called_once()
        self.assertTrue(app.root.winfo_exists())

    def test_close_is_refused_under_a_message_box(self):
        """Tk's own message boxes (the card prompt, alerts) hold the grab under a name tkinter cannot map."""
        from tkinter import messagebox
        app = self.app_with_pins()
        app.root.update()
        if app.root.tk.call("tk", "windowingsystem") != "x11":
            self.skipTest("native message boxes disable the window instead of taking a Tk grab")
        errors = []
        app.root.report_callback_exception = lambda *args: errors.append(args[1])
        app.worker = SimpleNamespace(is_alive=lambda: True)  # card writing in progress
        app.session = SimpleNamespace(cleanup=mock.Mock())
        seen = []

        def close_then_dismiss(name):
            with mock.patch("ykrecover.gui.messagebox.showinfo") as notice:
                app.root.tk.call(app.root.protocol("WM_DELETE_WINDOW"))
            seen.append((name, notice.call_count, app.root.winfo_exists()))
            app.root.tk.call("destroy", name)

        when_dialog_up(app.root, close_then_dismiss)
        messagebox.askokcancel("Insert card", "Insert card 1 of 2.", parent=app.root)  # as wait_for_card does
        self.assertEqual(errors, [])
        self.assertEqual(seen, [(".__tk__messagebox", 0, 1)])
        app.session.cleanup.assert_not_called()

    def test_close_under_the_pin_dialog_does_not_nest_a_second_one(self):
        from ykrecover.gui import TextDialog
        app = self.app_with_pins()
        seen = []

        def close_then_confirm(dialog):
            app.close()  # refused: the dialog holds the grab
            app.reveal_pins()  # re-entrant call: nothing left to show
            seen.append(sum(isinstance(w, TextDialog) for w in app.root.winfo_children()))
            dialog.done(True)

        when_dialog_up(app.root, close_then_confirm)
        app.reveal_pins()
        self.assertEqual(seen, [1])
        self.assertEqual(app.pins, [])
        self.assertTrue(app.pins_shown)
        self.assertTrue(app.root.winfo_exists())

    def test_termination_signal_reveals_pins_and_closes(self):
        app = self.app_with_pins()
        app.request_terminate(signal.SIGTERM, None)
        with shown_pins() as shown:
            app.poll()
        self.assertEqual(shown.call_count, 1)
        self.assertIn("Admin PIN 12345678", shown.call_args.args[0].text_content)
        with self.assertRaises(tk.TclError):
            app.root.winfo_exists()  # destroyed

    def test_termination_mid_run_reveals_changed_pins(self):
        app = self.app_with_pins()
        app.pins = []
        changed = SimpleNamespace(serial="22222222", admin_pin="87654321", user_pin="654321", pins_set=True)
        untouched = SimpleNamespace(serial="", admin_pin="11112222", user_pin="333444", pins_set=False)
        app.session = SimpleNamespace(results=[changed, untouched], cleanup=mock.Mock())
        app.worker = SimpleNamespace(is_alive=lambda: True)
        with shown_pins() as shown:
            app.terminate()
        self.assertIn("Card 22222222: Admin PIN 87654321", shown.call_args.args[0].text_content)
        self.assertNotIn("11112222", shown.call_args.args[0].text_content)
        app.session.cleanup.assert_called_once()

    def backup_page_with_leftovers(self):
        """An app on the backup page whose work folder holds a leftover, so leave_backup() warns."""
        from tests.helpers import FIXTURES, short_tempdir
        from ykrecover import osdeps
        app = self.app_with_pins()
        app.pins = []
        self.addCleanup(osdeps.set_workdir, None)
        workdir = short_tempdir(self, "wd")
        leftover = workdir / "ykr-staging-old"
        leftover.mkdir()
        os.utime(leftover, (1, 1))
        app.v["folder"].set(str(FIXTURES / "ed25519" / "export"))
        app.v["workdir"].set(str(workdir))
        app.show(1)
        app.root.update()
        return app

    def test_termination_under_a_backup_page_dialog_stops_loading(self):
        """poll() runs inside a dialog's own event loop: a signal there destroys the window under it, and
        leave_backup() must not go on to load and unlock the key."""
        from ykrecover.backup import AmbiguousKey
        from ykrecover.gui import TITLE
        from ykrecover.migrate import Session
        bundles = [SimpleNamespace(key_fp="A" * 40, uids=("one",)), SimpleNamespace(key_fp="B" * 40, uids=("two",))]
        for title, load in (("Work folder", None), (TITLE, AmbiguousKey(bundles))):
            with self.subTest(title):
                app = self.backup_page_with_leftovers()
                seen = []

                def signal_then_answer(dialog):
                    seen.append(dialog.title())
                    if dialog.title() == title:
                        app.request_terminate(signal.SIGTERM, None)  # acted on by the next poll()
                    else:  # the leftover warning, before the key chooser
                        dialog.done(None)
                        when_dialog_up(app.root, signal_then_answer)

                when_dialog_up(app.root, signal_then_answer)
                with mock.patch.object(Session, "load", side_effect=load) as loaded, \
                        mock.patch.object(Session, "unlock", return_value=True) as unlocked:
                    self.assertFalse(app.leave_backup())
                self.assertFalse(app.alive())
                self.assertEqual(seen, ["Work folder"] if load is None else ["Work folder", TITLE])
                self.assertEqual(loaded.call_count, 0 if load is None else 1)
                unlocked.assert_not_called()

    def test_a_dialog_destroyed_before_it_shows_returns_its_close_value(self):
        from ykrecover.gui import TextDialog
        app = self.app_with_pins()
        dialog = TextDialog(app.root, "t", "h", "body", (("OK", 1),), close_value="closed")
        with mock.patch.object(TextDialog, "wait_visibility", side_effect=tk.TclError("deleted")):
            self.assertEqual(dialog.run(), "closed")  # as when terminate() destroys the root under it

    def test_termination_waits_until_the_pins_are_confirmed(self):
        app = self.app_with_pins()
        seen = []

        def signal_then_confirm(dialog):
            app.request_terminate(signal.SIGTERM, None)
            app.root.after(400, lambda: (seen.append((app.alive(), dialog.winfo_exists())), dialog.done(True)))

        when_dialog_up(app.root, signal_then_confirm)
        app.reveal_pins()  # as from the summary page's button: poll() keeps running under the dialog
        self.assertEqual(seen, [(True, 1)])  # several polls went by, and the PINs stayed on screen
        self.assertTrue(app.pins_shown)
        app.poll()
        self.assertFalse(app.alive())

    def test_back_to_backup_page_keeps_the_unlocked_session(self):
        from ykrecover.backup import Candidate
        from ykrecover.gui import App
        from ykrecover.migrate import Options
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = App(root)
        app.alert = lambda text: self.fail(text)
        folder = Path("/backup")
        app.show(1)
        detected(app, folder, [Candidate("export", folder, ())])  # a key found in files loads from the chosen folder

        def no_reload():
            raise AssertionError("reloaded")

        app.session = SimpleNamespace(opts=Options(backup=folder, source="export"), passphrase="secret",
                                      bundle=object(), load=no_reload, unlock=no_reload, cleanup=no_reload)
        self.assertTrue(app.leave_backup())  # empty passphrase field, but nothing reloaded or unlocked
        self.assertEqual(app.failures, 0)

    def test_back_keeps_a_key_chosen_from_several(self):
        from ykrecover.backup import Candidate
        from ykrecover.gui import App
        from ykrecover.migrate import Options
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = App(root)
        app.alert = lambda text: self.fail(text)
        home = Path("/backup/gnupg")
        app.show(1)
        detected(app, "/backup", [Candidate("gnupghome", home, ())])

        def no_reload():
            raise AssertionError("reloaded")

        # the key chooser set opts.key when the session loaded
        app.session = SimpleNamespace(opts=Options(backup=home, source="gnupghome", key="A" * 40), passphrase="secret",
                                      bundle=object(), load=no_reload, unlock=no_reload, cleanup=no_reload)
        self.assertTrue(app.leave_backup())

    def test_tkui_marshals_calls(self):
        from ykrecover.gui import App
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = App(root)
        app.ui.progress(1, "hello")  # queued, not executed on this thread
        self.assertFalse(app.tasks.empty())

    def test_poll_survives_failing_task(self):
        from ykrecover.gui import App
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = App(root)
        flag = []

        def boom():
            raise ValueError("boom")

        app.post(boom)
        app.post(lambda: flag.append(1))
        app.poll()
        self.assertEqual(flag, [1])
        self.assertIsNotNone(app.poll_id)  # rescheduled

    def test_worker_unexpected_error_posts_finished(self):
        from ykrecover.gui import App
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = App(root)

        class Stub:
            results = []

            def execute(self):
                raise OSError("boom")

        app.session = Stub()
        app.next_btn.state(["disabled"])
        app.logbox = tk.Text(root)
        app.work()
        app.poll()
        self.assertFalse(app.next_btn.instate(["disabled"]))
        text = app.logbox.get("1.0", "end")
        self.assertIn("error: OSError: boom", text)
        self.assertIn("finished with exit code 3", text)


FPR = "6359B871334E108D81DA7EF884CA380EC0FAA9DB"
DESCRIBE = "\n".join([f"Key {FPR}", "  uid Firstname Lastname-Longname <firstname.lastname@example-company.org>",
                      *(f"  [{u}] rsa4096 9418366136062EF6E76E67506A666220EEF5ACA5 expires 2028-10-11 <- card"
                        for u in "SEA")])
LONG_WARNINGS = (
    "network interface wlp0s20f3 is up; an air-gapped system is recommended",
    "temporary key copies are on disk at /tmp; use --workdir with a RAM disk or encrypted volume to avoid that",
    "left over from an earlier run: /tmp/ykr-staging-abcdef12; it may hold key material — delete it securely",
)
# tk scaling at 100%, 125% and 150% Windows display scaling (1.33 is 96 dpi).
SCALINGS = (96 / 72, 120 / 72, 144 / 72)


def fake_session(cards=10):
    from ykrecover.migrate import Options
    opts = Options(backup=Path("/media/user/ENCRYPTED-BACKUP/gnupg-backup"), out=Path("/media/user/PUBLIC/out"),
                   cards=cards)
    results = [SimpleNamespace(index=i, serial="12345678", status="FAILED",
                               detail="decrypt round-trip (PINs were changed; see PINs shown)")
               for i in range(1, cards + 1)]
    return SimpleNamespace(opts=opts, results=results, describe=lambda: DESCRIBE, cleanup=lambda: None,
                           bundle=SimpleNamespace(has_certify_secret=True))


@SKIP_GUI
class GuiLayoutTests(unittest.TestCase):
    """Every page and dialog fits its window at 100%, 125% and 150% display scaling."""

    def app(self, scaling):
        from ykrecover.gui import App
        from ykrecover.migrate import Session
        root = tk.Tk()
        self.addCleanup(root.destroy)
        root.tk.call("tk", "scaling", scaling)  # before any font is used

        def preflight(session):
            for warning in LONG_WARNINGS:
                session.ui.warn(warning)

        patcher = mock.patch.object(Session, "preflight", preflight)
        patcher.start()
        self.addCleanup(patcher.stop)
        app = App(root)
        app.alert = lambda text: None
        app.session = fake_session()
        return root, app

    def test_pages_fit_the_minimum_window(self):
        for scaling in SCALINGS:
            root, app = self.app(scaling)
            width, height = root.minsize()
            root.geometry(f"{width}x{height}")
            for page in range(len(app.pages)):
                with self.subTest(scaling=round(scaling, 2), page=page):
                    app.show(page)
                    root.update()
                    # fit_page did not have to grow it
                    self.assertEqual(root.minsize(), (width, height), self.layout(app))
                    self.assertLessEqual(root.winfo_reqwidth(), width)
                    self.assertLessEqual(root.winfo_reqheight(), height)
                    for button in (app.back_btn, app.next_btn):
                        self.assertTrue(button.winfo_ismapped())
                        self.assertLessEqual(button.winfo_y() + button.winfo_height(),
                                             button.master.winfo_height())
                        self.assertLessEqual(button.master.winfo_y() + button.master.winfo_height(),
                                             root.winfo_height())
                    for text in self.texts(app.body):
                        self.assertEqual(text.cget("wrap"), "word")  # no line runs off the side

    @staticmethod
    def layout(app) -> str:
        """What each widget of the page asks for, to find the one that outgrew the window."""
        from tkinter import font as tkfont
        fonts = {name: tkfont.nametofont(name, root=app.root).metrics("linespace")
                 for name in ("TkDefaultFont", "TkFixedFont")}
        rows = [f"{child.winfo_name()} ({child.winfo_class()}) {child.winfo_reqwidth()}x{child.winfo_reqheight()}"
                for child in app.body.winfo_children()]
        return (f"linespace {fonts}; body {app.body.winfo_reqheight()}, nav {app.next_btn.master.winfo_reqheight()}:"
                "\n  " + "\n  ".join(rows))

    def test_report_box_adds_about_one_line_to_the_backup_page(self):
        # A scrolled text asks for at least its scrollbar's minimum height (about 50 px on Windows at 133%), whatever
        # its line count; the report box must not, or the backup page outgrows the minimum window.
        for scaling in SCALINGS:
            root, app = self.app(scaling)
            app.show(1)
            root.update_idletasks()
            holder = app.body.nametowidget("report")
            with_report = app.body.winfo_reqheight()
            holder.pack_forget()
            root.update_idletasks()
            with self.subTest(scaling=round(scaling, 2)):
                self.assertEqual(holder.winfo_reqheight(), app.report_height())
                self.assertLessEqual(with_report - app.body.winfo_reqheight(), app.report_height(), self.layout(app))
                self.assertGreater(app.report_box.winfo_reqheight(), 0)

    def texts(self, widget):
        for child in widget.winfo_children():
            if isinstance(child, tk.Text):
                yield child
            yield from self.texts(child)

    def test_window_fits_a_1080p_screen_at_150_percent(self):
        root, _ = self.app(SCALINGS[2])
        width, height = root.minsize()
        self.assertLessEqual(width, 1920)
        self.assertLessEqual(height, 1080 - 150)  # title bar and taskbar

    def test_card_table_columns_fit(self):
        for scaling in SCALINGS:
            root, app = self.app(scaling)
            width, height = root.minsize()
            root.geometry(f"{width}x{height}")
            app.show(6)
            root.update()
            table = app.table
            self.assertLessEqual(root.winfo_reqwidth(), width)
            self.assertLessEqual(table.winfo_x() + table.winfo_width(), table.master.winfo_width())

    def test_dialogs_fit_the_screen(self):
        from ykrecover.gui import ChoiceDialog
        stderr = "\n".join(f"gpg: line {i}: card communication failed with a long scdaemon message"
                           for i in range(2000))
        for scaling in SCALINGS:
            root, app = self.app(scaling)
            dialogs = {
                "failure": app.failure_dialog("12345678", "gpg --edit-card", "gpg --edit-card\n" + stderr),
                "pins": app.pins_dialog([(f"1000000{i}", "12345678", "123456") for i in range(10)]),
                "keys": ChoiceDialog(root, "keys", "Choose a key.", [f"{FPR}  A Long Name <a.long@example.org>"] * 12),
            }
            for name, dialog in dialogs.items():
                with self.subTest(scaling=round(scaling, 2), dialog=name):
                    dialog.update_idletasks()
                    self.assertLessEqual(dialog.winfo_reqheight(), 0.8 * root.winfo_screenheight())
                    self.assertLessEqual(dialog.winfo_reqwidth(), 0.9 * root.winfo_screenwidth())
            self.assertFalse(dialogs["failure"].text_content.startswith("gpg --edit-card"))  # not repeated
            pins = dialogs["pins"].text
            self.assertEqual(pins.cget("wrap"), "none")  # a card's two PINs stay on one line
            self.assertGreaterEqual(pins.cget("width"), len("Card 10000000: Admin PIN 12345678   User PIN 123456"))

    def test_backup_page_fits_with_a_long_report(self):
        root, app = self.app(SCALINGS[2])
        width, height = root.minsize()
        root.geometry(f"{width}x{height}")
        app.show(1)
        app.set_report("\n".join(f"  folder/sub/file-{i:02d}.asc: key 0123456789ABCDEF: main secret, "
                                 f"3 subkeys secret (not needed)" for i in range(20)))
        root.update()
        self.assertEqual(root.minsize(), (width, height))
        self.assertLessEqual(root.winfo_reqheight(), height)


@SKIP_GUI
class GuiBehaviourTests(unittest.TestCase):
    def app(self):
        from ykrecover.gui import App
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = App(root)
        self.alerts = []
        app.alert = self.alerts.append
        app.session = fake_session(cards=2)
        self.addCleanup(lambda: app.session and app.session.cleanup())
        return app

    def test_closing_the_failure_dialog_aborts_but_the_pins_need_confirming(self):
        app = self.app()
        failure = app.failure_dialog("12345678", "gpg --edit-card", "gpg --edit-card\ngpg: card error")
        failure.tk.call(failure.protocol("WM_DELETE_WINDOW"))
        self.assertEqual(failure.value, "abort")
        self.assertFalse(failure.winfo_exists())
        pins = app.pins_dialog([("12345678", "11111111", "222222")])
        pins.tk.call(pins.protocol("WM_DELETE_WINDOW"))
        self.assertTrue(pins.winfo_exists())  # only "I wrote them down" closes it
        pins.destroy()

    def clipboard(self, app):
        try:
            return app.root.clipboard_get()
        except tk.TclError:  # empty
            return ""

    def test_copied_pins_leave_the_clipboard_on_confirm(self):
        app = self.app()
        pins = app.pins_dialog([("12345678", "11111111", "222222")])
        self.assertIn("clipboard is cleared", "".join(self.label_texts(pins)))
        pins.copy()
        self.assertIn("Admin PIN 11111111", self.clipboard(app))
        pins.done(True)
        self.assertNotIn("11111111", self.clipboard(app))

    def test_pins_copied_from_a_selection_leave_the_clipboard_on_confirm(self):
        app = self.app()
        for first, last in (("1.0", "end-1c"), ("1.25", "1.33")):  # all, and just the Admin PIN
            with self.subTest(selection=(first, last)):
                pins = app.pins_dialog([("12345678", "11111111", "222222")])
                pins.text.tag_add("sel", first, last)
                pins.text.event_generate("<<Copy>>")  # what Ctrl+C sends; the text is read-only, not unselectable
                self.assertIn("11111111", self.clipboard(app))
                pins.done(True)
                self.assertEqual(self.clipboard(app), "")

    def test_confirming_pins_keeps_what_was_copied_since(self):
        app = self.app()
        pins = app.pins_dialog([("12345678", "11111111", "222222")])
        pins.copy()
        app.root.clipboard_clear()
        app.root.clipboard_append("my password manager note")
        pins.done(True)
        self.assertEqual(self.clipboard(app), "my password manager note")
        other = app.failure_dialog("12345678", "step", "step\nerror text")
        other.copy()
        other.done("abort")  # other dialogs leave the clipboard alone
        self.assertEqual(self.clipboard(app), "error text")

    def label_texts(self, widget):
        out = []
        try:
            out.append(str(widget.cget("text")))
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            out.extend(self.label_texts(child))
        return out

    def test_key_chooser_works_from_the_keyboard(self):
        from ykrecover.gui import ChoiceDialog
        app = self.app()
        app.root.update()
        dialog = ChoiceDialog(app.root, "t", "Choose", ["first", "second", "third"])
        seen = []

        def press(dialog, *keys):
            # Tk delivers keys only to the focused widget, and a CI display without a window manager
            # never focuses the dialog itself; so force it, after checking where the dialog put it.
            dialog.update()
            seen.append(str(dialog.tk.call("focus", "-lastfor", dialog)) == str(dialog.listbox))
            dialog.listbox.focus_force()
            dialog.update()
            for key in keys:
                dialog.listbox.event_generate(key)

        when_dialog_up(app.root, lambda d: press(d, "<Down>", "<Return>"))
        self.assertEqual(dialog.run(), 1)
        dialog = ChoiceDialog(app.root, "t", "Choose", ["first", "second"])
        when_dialog_up(app.root, lambda d: press(d, "<Return>"))
        self.assertEqual(dialog.run(), 0)  # the first row is chosen without touching the list
        self.assertEqual(seen, [True, True])

    def test_return_on_a_focused_button_presses_that_button(self):
        from ykrecover.gui import ChoiceDialog
        app = self.app()
        app.root.update()
        seen = []
        for label, expected in (("Cancel", None), ("OK", 0)):
            with self.subTest(label):
                dialog = ChoiceDialog(app.root, "t", "Choose", ["first", "second"])
                button = next(b for b in dialog.bar.winfo_children() if b.cget("text") == label)

                def press(dialog):
                    button.focus_force()  # as after Tab; forced, since there may be no window manager
                    dialog.update()
                    button.event_generate("<Return>")
                    seen.append(dialog.winfo_exists())  # closed by the key, not by the helper's timeout

                when_dialog_up(app.root, press)
                self.assertEqual(dialog.run(), expected)
        self.assertEqual(seen, [0, 0])

    def test_settings_are_validated_on_their_page(self):
        app = self.app()
        app.show(4)
        app.v["retries"].set(" 3, 4 ,5 ")
        self.assertTrue(app.leave_settings())
        self.assertEqual(app.session.opts.retries, ("3", "4", "5"))
        app.v["retries"].set("5,5")
        app.v["name"].set("Bad <name>")
        app.v["cards"].set("0")
        self.assertFalse(app.leave_settings())
        self.assertIn("Retries must be three numbers from 1 to 99", self.alerts[-1])
        self.assertIn("Cardholder name must be", self.alerts[-1])
        self.assertIn("Number of cards", self.alerts[-1])
        self.assertNotIn("--", self.alerts[-1])

    def test_output_folder_errors_use_field_names(self):
        from ykrecover.gui import gui_wording
        app = self.app()
        app.show(5)
        self.assertFalse(app.leave_output())
        self.assertEqual(self.alerts, ["Choose an output folder."])
        self.assertEqual(gui_wording("--out must be outside the backup folder"),
                         "The output folder must be outside the backup folder")
        self.assertEqual(gui_wording("cannot create the --out folder /x: denied"),
                         "Cannot create the output folder /x: denied")

    def test_back_works_on_the_card_page_until_start(self):
        app = self.app()
        app.show(6)
        self.assertFalse(app.back_btn.instate(["disabled"]))
        with mock.patch("ykrecover.gui.threading.Thread"):
            app.start()
        self.assertTrue(app.back_btn.instate(["disabled"]))
        app.show(6)
        self.assertTrue(app.back_btn.instate(["disabled"]))

    def test_card_table_shows_step_names_and_the_log_full_commands(self):
        app = self.app()
        app.show(6)
        command = "ykman --device 12345678 openpgp keys set-touch sig on -f -a <ADMIN_PIN>"
        app.ui.progress(1, f"12345678: {command}")
        app.poll()
        self.assertEqual(app.table.item("1", "values"), ("12345678: set touch policy",))
        self.assertIn(command, app.logbox.get("1.0", "end"))

    def on_worker(self, app, fn):
        """Runs fn on a worker thread, as Session does, while this thread serves its queued dialogs."""
        import threading
        from ykrecover import Aborted
        outcome = []

        def worker():
            try:
                outcome.append(fn())
            except Aborted:
                outcome.append("aborted")

        thread = threading.Thread(target=worker)
        thread.start()
        while thread.is_alive():
            app.poll()
            thread.join(0.05)
        return outcome[0]

    def test_cancel_at_a_confirmation_aborts(self):
        app = self.app()
        with mock.patch("ykrecover.gui.messagebox.askyesnocancel", return_value=None):
            self.assertEqual(self.on_worker(app, lambda: app.ui.confirm("Saved it to offline storage?")), "aborted")
        with mock.patch("ykrecover.gui.messagebox.askyesnocancel", return_value=False):
            self.assertIs(self.on_worker(app, lambda: app.ui.confirm("Saved it to offline storage?")), False)

    def test_failed_card_choice_comes_from_the_dialog(self):
        from ykrecover.gui import TextDialog
        app = self.app()
        with mock.patch.object(TextDialog, "run", autospec=True, return_value="skip") as shown:
            answer = self.on_worker(app, lambda: app.ui.card_failed(1, "12345678", "change PIN",
                                                                   "change PIN\ngpg: Bad PIN"))
        self.assertEqual(answer, "skip")
        self.assertEqual(shown.call_args.args[0].text_content, "gpg: Bad PIN")

    def test_several_keys_offer_a_chooser(self):
        from ykrecover.backup import AmbiguousKey, Candidate
        from ykrecover.gui import ChoiceDialog
        from ykrecover.migrate import Session
        app = self.app()
        app.session = None
        app.show(1)
        detected(app, "/backup", [Candidate("export", Path("/backup"), ())])
        bundles = [SimpleNamespace(key_fp="A" * 40, uids=("one",)), SimpleNamespace(key_fp="B" * 40, uids=("two",))]
        with mock.patch.object(Session, "load", side_effect=[AmbiguousKey(bundles), None]), \
                mock.patch.object(Session, "unlock", return_value=True), \
                mock.patch.object(ChoiceDialog, "run", return_value=1):
            self.assertTrue(app.leave_backup())
        self.assertEqual(app.session.opts.key, "B" * 40)

    def test_work_folder_applies_only_once_the_session_is_adopted(self):
        from ykrecover import osdeps
        from ykrecover.migrate import Options, Session
        from tests.helpers import short_tempdir
        self.addCleanup(osdeps.set_workdir, None)
        osdeps.set_workdir(None)
        workdir = short_tempdir(self, "adopt")
        Session(Options(backup=Path("/backup"), workdir=workdir), None).validate_workdir(apply=False)
        self.assertEqual(osdeps.work_base(), osdeps.default_work_base())
        Session(Options(backup=Path("/backup"), workdir=workdir), None).validate_workdir()
        self.assertEqual(osdeps.work_base(), workdir.resolve())

    def test_detection_errors_are_shown_beside_the_list(self):
        app = self.app()
        app.v["folder"].set("/nonexistent")
        app.show(1)
        self.assertEqual(app.cand_list.size(), 0)
        self.assertTrue(app.cand_error.cget("text"))

    def test_backup_page_shows_the_report(self):
        app = self.app()
        app.show(1)
        app.v["folder"].set(str(FIXTURES / "flexible" / "asc backup"))
        app.refresh_candidates()
        text = app.report_box.get("1.0", "end")
        self.assertIn("private/flex.priv.asc", text)
        self.assertIn("(used)", text)
        self.assertIn("key " + fixture_info("flexible")["KEY_ID"], app.cand_list.get(0))

    def test_unprotected_candidate_disables_the_passphrase(self):
        app = self.app()
        app.show(1)
        app.v["folder"].set(str(FIXTURES / "unprotected"))
        app.refresh_candidates()
        app.cand_list.selection_set(0)
        app.cand_list.event_generate("<<ListboxSelect>>")
        app.root.update()
        self.assertIn("disabled", app.pass_entry.state())

    def test_key_candidates_load_from_the_root_folder(self):
        app = self.app()
        app.show(1)
        app.v["folder"].set(str(FIXTURES / "split"))
        app.refresh_candidates()
        app.cand_list.selection_set(0)
        app.v["passphrase"].set(PASSPHRASE)
        self.assertTrue(app.leave_backup())
        self.assertEqual(app.session.opts.backup, FIXTURES / "split")
        self.assertTrue(app.session.bundle.has_certify_secret)


    def test_next_redetects_a_folder_typed_after_detect(self):
        app = self.app()
        app.session = None
        app.show(1)
        app.v["folder"].set(str(FIXTURES / "split"))
        app.refresh_candidates()
        app.cand_list.selection_set(0)
        app.v["passphrase"].set(PASSPHRASE)
        app.v["folder"].set(str(FIXTURES / "unprotected"))
        app.next()
        self.assertEqual(app.page, 1)
        self.assertIsNone(app.session)
        self.assertEqual(app.inventory.root, FIXTURES / "unprotected")
        self.assertEqual(app.cand_list.size(), 1)
        self.assertIn("key " + fixture_info("unprotected")["KEY_ID"], app.cand_list.get(0))

if __name__ == "__main__":
    unittest.main()
