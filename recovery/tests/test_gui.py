import os
import signal
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    import tkinter as tk
except ImportError:  # python3-tk not installed
    tk = None


@unittest.skipIf(tk is None or not os.environ.get("DISPLAY"), "needs tkinter and a display")
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
        app.v["folder"].set(str(FIXTURES / "ed25519" / "export"))
        app.v["workdir"].set(str(workdir))
        app.v["passphrase"].set(PASSPHRASE)
        app.show(1)
        root.update()
        self.assertTrue(app.leave_backup())
        self.assertEqual(Path(app.session.staging.path).parent, workdir)
        self.assertIn(str(workdir), app.workdir_note.cget("text"))

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
        with mock.patch("ykrecover.gui.messagebox.showinfo"):
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
        with mock.patch("ykrecover.gui.messagebox.showinfo") as shown:
            app.finished(0)
            app.reveal_pins()  # once only
        self.assertEqual(shown.call_count, 1)
        self.assertIn("Admin PIN 12345678", shown.call_args.args[1])
        self.assertEqual(app.pins, [])
        app.session = SimpleNamespace(opts=SimpleNamespace(out="/out", cards=2), results=[])
        app.show(7)
        self.assertFalse(hasattr(app, "pin_btn"))
        summary = next(w for w in app.body.winfo_children() if isinstance(w, tk.Text)).get("1.0", "end")
        self.assertIn("update-stubs --import <public key file> --remove-serial <old serial> --cards 2", summary)

    def test_close_shows_pins_never_shown(self):
        app = self.app_with_pins()
        with mock.patch("ykrecover.gui.messagebox.showinfo") as shown:
            app.close()
        self.assertEqual(shown.call_count, 1)
        self.assertIn("User PIN 123456", shown.call_args.args[1])

    def test_termination_signal_reveals_pins_and_closes(self):
        app = self.app_with_pins()
        app.request_terminate(signal.SIGTERM, None)
        with mock.patch("ykrecover.gui.messagebox.showinfo") as shown:
            app.poll()
        self.assertEqual(shown.call_count, 1)
        self.assertIn("Admin PIN 12345678", shown.call_args.args[1])
        with self.assertRaises(tk.TclError):
            app.root.winfo_exists()  # destroyed

    def test_termination_mid_run_reveals_changed_pins(self):
        app = self.app_with_pins()
        app.pins = []
        changed = SimpleNamespace(serial="22222222", admin_pin="87654321", user_pin="654321", pins_set=True)
        untouched = SimpleNamespace(serial="", admin_pin="11112222", user_pin="333444", pins_set=False)
        app.session = SimpleNamespace(results=[changed, untouched], cleanup=mock.Mock())
        app.worker = SimpleNamespace(is_alive=lambda: True)
        with mock.patch("ykrecover.gui.messagebox.showinfo") as shown:
            app.terminate()
        self.assertIn("Card 22222222: Admin PIN 87654321", shown.call_args.args[1])
        self.assertNotIn("11112222", shown.call_args.args[1])
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


if __name__ == "__main__":
    unittest.main()
