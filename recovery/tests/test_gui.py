import os
import unittest

try:
    import tkinter as tk
except ImportError:  # python3-tk not installed
    tk = None


@unittest.skipIf(tk is None or not os.environ.get("DISPLAY"), "needs tkinter and a display")
class GuiSmokeTests(unittest.TestCase):
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
