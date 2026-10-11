import os
import signal
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.helpers import LINUX

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
        app.candidates = [Candidate("export", Path("/backup"), ())]
        app.cand_list.insert("end", "export: /backup")
        app.cand_list.selection_set(0)
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
        app.candidates = [Candidate("export", folder, ())]
        app.cand_list.insert("end", "export: /backup")
        app.cand_list.selection_set(0)

        def no_reload():
            raise AssertionError("reloaded")

        app.session = SimpleNamespace(opts=Options(backup=folder, source="export"), passphrase="secret",
                                      bundle=object(), load=no_reload, unlock=no_reload, cleanup=no_reload)
        self.assertTrue(app.leave_backup())  # empty passphrase field, but nothing reloaded or unlocked
        self.assertEqual(app.failures, 0)

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
            app.show(6)
            columns = sum(app.table.column(c, "width") for c in ("#0", "status"))
            self.assertLessEqual(columns, root.minsize()[0] - 48)

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
        app.candidates = [Candidate("export", Path("/backup"), ())]
        app.cand_list.insert("end", "export: /backup")
        app.cand_list.selection_set(0)
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


if __name__ == "__main__":
    unittest.main()
