"""Tkinter wizard over migrate.Session.

Card work runs on a worker thread; every UI call it makes is queued to the Tk main loop,
which owns all widgets and dialogs.
"""

import queue
import signal
import sys
import threading
import traceback
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import EXIT_CARD, EXIT_INTERRUPT, TERMINATION_SIGNALS, Aborted, RecoveryError, osdeps
from . import backup as backup_mod
from .card import TOUCH_POLICIES
from .migrate import (Options, Session, update_stubs_command, update_stubs_where, valid_metadata,
                      valid_retries)

TITLE = "YubiKey recovery"
METADATA_WARNING = ("OpenPGP card metadata (name, login, URL) can be read by anyone holding the card. "
                    "Leave the name blank unless you want it for lost-and-found.")
MODE_HELP = ("clone: write the backed-up subkeys as they are.\n"
             "renew: extend their expiry first (needs the Certify key).\n"
             "rotate: create new subkeys (needs the Certify key). Choose this if the old cards may be "
             "lost or compromised.")
# Window size in units of the default font, so it grows with Windows display scaling (125%, 150%).
WINDOW_EMS = (84, 32)  # width in "0" widths, height in line heights
# Core errors name CLI flags; the wizard names its fields instead. Checked in order.
GUI_WORDS = (
    ("cannot create the --out folder", "cannot create the output folder"),
    ("--algo and --revoke-old need --mode rotate", "a new algorithm and revoking old subkeys need mode rotate"),
    ("--out", "the output folder"), ("--workdir", "the work folder"), ("--mode ", "mode "),
    ("--cards", "the number of cards"), ("--name", "the cardholder name"), ("--login", "the login"),
    ("--url", "the public key URL"),
)
# Short names for the card table; the log keeps the full commands. Matched as whole words, in order.
STEP_LABELS = (
    ("openpgp reset", "reset OpenPGP"), ("set-retries", "set PIN retries"), ("--change-pin", "change PIN"),
    ("--edit-card", "set cardholder data"), ("--edit-key", "move subkey to card"), ("set-touch", "set touch policy"),
    ("--card-status", "check card keys"), ("--detach-sign", "test signing"), ("--verify", "verify signature"),
    ("--encrypt", "test encryption"), ("--decrypt", "test decryption"), ("--export", "export public key"),
    ("--import", "import public key"), ("--kill", "restart GnuPG"),
)


def gui_wording(message) -> str:
    """A core error message with the CLI flags replaced by the wizard's field names."""
    text = str(message)
    for flag, words in GUI_WORDS:
        text = text.replace(flag, words)
    return text[:1].upper() + text[1:]


def short_status(status: str) -> str:
    """'<serial>: <command>' as '<serial>: <step name>'; other statuses stay as they are."""
    serial, sep, command = status.partition(": ")
    if not sep:
        return status
    padded = f" {command} "
    for needle, label in STEP_LABELS:
        if f" {needle} " in padded:
            return f"{serial}: {label}"
    return f"{serial}: {' '.join(command.split()[:2])}"


def scrolled_text(parent, height, wrap="word", width=1):
    """A Text with scrollbars (horizontal too when lines do not wrap) in its own frame: (frame, text).

    width=1 lets the layout, not the 80-character default, decide the width.
    """
    frame = ttk.Frame(parent)
    text = tk.Text(frame, width=width, height=height, wrap=wrap)
    yscroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=yscroll.set)
    text.grid(row=0, column=0, sticky="nsew")
    yscroll.grid(row=0, column=1, sticky="ns")
    if wrap == "none":
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
        text.configure(xscrollcommand=xscroll.set)
        xscroll.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    # Wrapped lines start under the text, not at the margin.
    indent = tkfont.Font(root=text, font=text.cget("font")).measure("0") * 4
    text.tag_configure("hang", lmargin2=indent)
    return frame, text


def set_text(text, content):
    text.configure(state="normal")
    text.delete("1.0", "end")
    text.insert("end", content, ("hang",))
    text.configure(state="disabled")


class Modal(tk.Toplevel):
    """A modal dialog: a wrapped header, a body (self.content) and a row of buttons.

    Build it, then run() shows it, waits and returns the value of the button pressed. Tests build
    without running it to measure the layout.
    """

    def __init__(self, parent, title, header, buttons, closable=True, close_value=None):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.transient(parent)
        self.parent = parent
        self.value = close_value
        self.focus_widget = self  # what run() gives the keyboard focus
        self.em = tkfont.nametofont("TkDefaultFont", root=self).measure("0")
        self.max_width = int(self.winfo_screenwidth() * 0.8)  # for the header and the body
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        self.bar = ttk.Frame(outer)
        self.bar.pack(side="bottom", fill="x", pady=(12, 0))  # first, so a short window never hides it
        for label, value in reversed(buttons):
            ttk.Button(self.bar, text=label, command=lambda v=value: self.done(v)).pack(side="right", padx=(4, 0))
        ttk.Label(outer, text=header, wraplength=min(self.em * 60, self.max_width), justify="left").pack(
            anchor="w", pady=(0, 8))
        self.content = ttk.Frame(outer)
        self.content.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", (lambda: self.done(close_value)) if closable else self.bell)
        self.bind("<Escape>", lambda event: self.done(close_value) if closable else None)

    def lines_that_fit(self, wanted, font, share=0.45):
        """Text lines to request: at most wanted, and no more than share of the screen height."""
        line = tkfont.Font(root=self, font=font).metrics("linespace")
        return max(3, min(wanted, int(self.winfo_screenheight() * share / line)))

    def done(self, value):
        self.value = value
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def run(self):
        self.update_idletasks()
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        x = self.parent.winfo_rootx() + (self.parent.winfo_width() - width) // 2
        y = self.parent.winfo_rooty() + (self.parent.winfo_height() - height) // 3
        x = max(0, min(x, self.winfo_screenwidth() - width))
        y = max(0, min(y, self.winfo_screenheight() - height))
        self.geometry(f"+{x}+{y}")
        self.deiconify()
        self.wait_visibility()
        self.grab_set()
        self.focus_widget.focus_set()
        self.wait_window()
        return self.value


class TextDialog(Modal):
    """Read-only, scrollable text (error output, PINs) with a Copy button."""

    def __init__(self, parent, title, header, content, buttons, wrap="word", lines=12, closable=True,
                 close_value=None, clear_clipboard=False):
        super().__init__(parent, title, header, buttons, closable, close_value)
        self.text_content = content
        self.clear_clipboard = clear_clipboard  # on close, unless something else was copied since
        self.copied = False
        frame, self.text = scrolled_text(self.content, 1, wrap)
        font = self.text.cget("font")
        longest = max((len(line) for line in content.splitlines()), default=0)
        chars = max(40, min(longest + 2, 72)) if wrap == "none" else 64
        fixed = tkfont.Font(root=self, font=font).measure("0")
        chars = min(chars, self.max_width // fixed)
        self.text.configure(width=chars, height=self.lines_that_fit(lines, font))
        set_text(self.text, content)
        frame.pack(fill="both", expand=True)
        ttk.Button(self.bar, text="Copy", command=self.copy).pack(side="left")

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.text_content)
        self.copied = True

    def done(self, value):
        if self.clear_clipboard and self.copied:
            try:
                if self.clipboard_get() == self.text_content:
                    self.clipboard_clear()
            except tk.TclError:  # empty, or holds something that is not text
                pass
        super().done(value)


class ChoiceDialog(Modal):
    """Pick one line from a list; run() returns its index, or None when cancelled."""

    def __init__(self, parent, title, header, items):
        super().__init__(parent, title, header, (("OK", "ok"), ("Cancel", None)))
        frame = ttk.Frame(self.content)
        frame.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(frame, width=min(60, self.max_width // self.em),
                                  height=min(max(len(items), 3), 8), exportselection=False)
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.listbox.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.listbox.xview)
        self.listbox.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        for item in items:
            self.listbox.insert("end", item)
        self.listbox.selection_set(0)
        self.listbox.activate(0)
        self.focus_widget = self.listbox  # arrow keys move the selection, Return chooses
        self.listbox.bind("<Double-Button-1>", lambda event: self.done("ok"))
        self.bind("<Return>", lambda event: self.done("ok"))

    def done(self, value):
        chosen = self.listbox.curselection() if value == "ok" else ()
        if value == "ok" and not chosen:
            self.bell()
            return
        super().done(chosen[0] if chosen else None)


class _Collector:
    def __init__(self, sink):
        self.sink = sink

    def warn(self, msg):
        self.sink.append(msg)


class TkUI:
    """migrate.Session's UI, safe to call from the worker thread."""

    def __init__(self, app):
        self.app = app

    def _ask(self, fn):
        done, box = threading.Event(), {}

        def task():
            try:
                box["value"] = fn()
            finally:
                done.set()

        self.app.tasks.put(task)
        done.wait()
        return box.get("value")

    def output(self, text):
        self.app.post(lambda: self.app.log(text))

    def info(self, msg):
        self.app.post(lambda: self.app.log(msg))

    def warn(self, msg):
        self.app.post(lambda: self.app.log(f"warning: {msg}"))

    def error(self, msg):
        self.app.post(lambda: self.app.log(f"error: {msg}"))

    def ask_secret(self, prompt):
        value = self._ask(lambda: simpledialog.askstring(TITLE, prompt, show="*", parent=self.app.root))
        if value is None:
            raise Aborted()
        return value

    def confirm(self, prompt):
        answer = self._ask(lambda: messagebox.askyesnocancel(TITLE, f"{prompt}\n\nCancel stops the run.",
                                                             parent=self.app.root))
        if answer is None:
            raise Aborted()
        return answer

    def confirm_reset(self, serial):
        typed = self._ask(lambda: simpledialog.askstring(
            "Erase card", f"All OpenPGP data on card {serial} will be erased.\nType RESET {serial} to continue:",
            parent=self.app.root))
        return typed == f"RESET {serial}"

    def wait_for_card(self, index, total):
        if not self._ask(lambda: messagebox.askokcancel(
                "Insert card", f"Insert card {index} of {total}. Only that YubiKey may be connected.",
                parent=self.app.root)):
            raise Aborted()

    def card_failed(self, index, serial, step, error):
        return self._ask(lambda: self.app.failure_dialog(serial, step, error).run())

    def progress(self, index, status):
        def task():
            self.app.log(f"card {index}: {status}")
            self.app.set_card(index, short_status(status))

        self.app.post(task)

    def show_pins(self, pins):
        self.app.post(lambda: self.app.keep_pins(pins))


class App:
    def __init__(self, root):
        self.root = root
        root.title(TITLE)
        font = tkfont.nametofont("TkDefaultFont", root=root)
        self.em = font.measure("0")
        width = min(self.em * WINDOW_EMS[0], root.winfo_screenwidth() - 40)
        height = min(font.metrics("linespace") * WINDOW_EMS[1], root.winfo_screenheight() - 80)
        root.minsize(width, height)
        root.geometry(f"{width}x{height}")
        root.option_add("*Dialog.msg.wrapLength", "6i")  # Tk's own message boxes wrap long lines
        # Treeview rows have a fixed pixel height that does not follow display scaling.
        ttk.Style(root).configure("Treeview", rowheight=font.metrics("linespace") + 4)
        self.heading_font = font.copy()
        self.heading_font.configure(size=14, weight="bold")
        self.wrap = width - 32  # wraplength of page text: the body width less its padding
        self.wrapped = []
        self.alert = lambda text: messagebox.showerror(TITLE, text, parent=self.root)
        self.tasks = queue.Queue()
        self.ui = TkUI(self)
        self.session = None
        self.candidates = []
        self.failures = 0
        self.pins = []  # kept PINs not yet shown
        self.pins_shown = False
        self.terminate_requested = False
        self.worker = None
        self.table = None
        self.logbox = None
        self.v = {
            "folder": tk.StringVar(), "workdir": tk.StringVar(), "passphrase": tk.StringVar(), "mode": tk.StringVar(value="clone"),
            "expire": tk.StringVar(value="2y"), "algo": tk.StringVar(value=""), "revoke": tk.BooleanVar(),
            "cards": tk.StringVar(value="1"), "pins": tk.StringVar(value="generate"), "same": tk.BooleanVar(),
            "retries": tk.StringVar(value="5,5,5"), "sig": tk.StringVar(value="on"),
            "dec": tk.StringVar(value="on"), "aut": tk.StringVar(value="on"), "name": tk.StringVar(),
            "login": tk.StringVar(), "url": tk.StringVar(), "out": tk.StringVar(),
        }
        nav = ttk.Frame(root, padding=(12, 0, 12, 12))
        nav.pack(side="bottom", fill="x")  # first, so a short window clips the page, never Back/Next
        self.body = ttk.Frame(root, padding=12)
        self.body.pack(fill="both", expand=True)
        self.body.bind("<Configure>", self.rewrap)
        self.back_btn = ttk.Button(nav, text="Back", command=lambda: self.show(self.page - 1))
        self.back_btn.pack(side="left")
        self.next_btn = ttk.Button(nav, text="Next", command=self.next)
        self.next_btn.pack(side="right")
        self.pages = [
            (self.build_preflight, None), (self.build_backup, self.leave_backup), (self.build_key, None),
            (self.build_mode, self.leave_mode), (self.build_settings, self.leave_settings),
            (self.build_output, self.leave_output), (self.build_cards, None), (self.build_summary, None),
        ]
        self.page = 0
        self.workdir_note = None
        self.v["workdir"].trace_add("write", lambda *_: self.update_workdir_note())
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_id = root.after(100, self.poll)
        root.bind("<Destroy>", self.stop_polling)
        self.show(0)

    # plumbing
    def post(self, fn):
        self.tasks.put(fn)

    def poll(self):
        if self.poll_id:
            self.root.after_cancel(self.poll_id)  # a direct call must not start a second chain
        reschedule = True
        try:
            if self.terminate_requested:
                self.terminate()
                reschedule = False  # the window is destroyed: a new "after" would outlive it
                return
            while True:
                try:
                    task = self.tasks.get_nowait()
                except queue.Empty:
                    break
                try:
                    task()
                except Exception as err:
                    try:
                        self.log(f"error: {err}")
                    except Exception:
                        pass
        finally:
            self.poll_id = None
            if reschedule:
                try:
                    self.poll_id = self.root.after(100, self.poll)
                except tk.TclError:  # window already destroyed
                    pass

    def stop_polling(self, event):
        if event.widget is self.root and self.poll_id:
            self.root.after_cancel(self.poll_id)
            self.poll_id = None

    def show(self, page):
        for child in self.body.winfo_children():
            child.destroy()
        self.page = page
        self.wrapped = []
        before_start = page == 6 and self.worker is None
        self.back_btn.state(["!disabled"] if 0 < page < 6 or before_start else ["disabled"])
        self.next_btn.state(["!disabled"] if page < 6 else ["disabled"])
        self.pages[page][0]()
        self.fit_page()

    def fit_page(self):
        # The font-based minimum fits every page on the fonts we test; grow it if this system's fonts need more.
        root = self.root
        root.update_idletasks()
        min_width, min_height = root.minsize()
        width = min(max(min_width, root.winfo_reqwidth()), root.winfo_screenwidth() - 40)
        height = min(max(min_height, root.winfo_reqheight()), root.winfo_screenheight() - 80)
        if (width, height) != (min_width, min_height):
            root.minsize(width, height)

    def next(self):
        leave = self.pages[self.page][1]
        if leave is None or leave():
            self.show(self.page + 1)

    def rewrap(self, event):
        self.wrap = max(event.width - 32, self.em * 20)  # padding 12 on each side, and some slack
        for label in self.wrapped:
            if label.winfo_exists():
                label.configure(wraplength=self.wrap)

    def wrapped_label(self, parent=None, **options):
        """A label whose text wraps at the current page width."""
        label = ttk.Label(parent or self.body, wraplength=self.wrap, justify="left", **options)
        self.wrapped.append(label)
        return label

    def heading(self, text, detail=""):
        ttk.Label(self.body, text=text, font=self.heading_font).pack(anchor="w")
        if detail:
            self.wrapped_label(text=detail).pack(anchor="w", fill="x", pady=(4, 12))

    def row(self, label, make):
        frame = ttk.Frame(self.body)
        frame.pack(fill="x", pady=2)
        ttk.Label(frame, text=label, width=26).pack(side="left")
        widget = make(frame)
        widget.pack(side="left", fill="x", expand=True)
        return widget

    def readonly_text(self, content, height):
        frame, text = scrolled_text(self.body, height)
        set_text(text, content)
        frame.pack(fill="both", expand=True)
        return text

    def log(self, text):
        if self.logbox is not None and self.logbox.winfo_exists():
            self.logbox.insert("end", text.rstrip("\n") + "\n", ("hang",))
            self.logbox.see("end")

    def set_card(self, index, status):
        if self.table is not None and self.table.winfo_exists() and self.table.exists(str(index)):
            self.table.item(str(index), values=(status,))

    def keep_pins(self, pins):
        self.pins = list(pins)

    def failure_dialog(self, serial, step, error):
        """Retry, skip or abort a failed card; shows only the error output under a one-line header.

        Closing the window aborts, as Cancel did in the message box this replaces."""
        output = error[len(step):] if error.startswith(step) else error  # StepError text starts with the step
        return TextDialog(self.root, "Card failed", f"Card {serial} failed at: {step}\n"
                          "Retry after fixing the problem, skip this card, or abort the run.",
                          output.strip() or "(no error output)",
                          (("Retry", "retry"), ("Skip", "skip"), ("Abort", "abort")), close_value="abort")

    def pins_dialog(self, pins):
        text = "\n".join(f"Card {s}: Admin PIN {a}   User PIN {u}" for s, a, u in pins)
        return TextDialog(self.root, "PINs", "Write these PINs down now. They are not shown again.\n"
                          "If you copy them, the clipboard is cleared when you confirm.", text,
                          (("I wrote them down", True),), wrap="none", lines=len(pins), closable=False,
                          clear_clipboard=True)

    # pages
    def build_preflight(self):
        self.heading("1. Preflight", "Checks tools and the environment. An air-gapped system is recommended.")
        warnings = []
        try:
            Session(Options(backup=Path(".")), _Collector(warnings)).preflight()
            lines = ["All required tools found.", *(f"warning: {w}" for w in warnings)]
        except RecoveryError as err:
            lines = [str(err)]
            self.next_btn.state(["disabled"])
        self.readonly_text("\n".join(lines), 6)

    def build_backup(self):
        self.heading("2. Backup", "Choose the folder holding your backup, for example the mounted encrypted "
                                  "USB volume. It is only read, never changed.")
        frame = ttk.Frame(self.body)
        frame.pack(fill="x")
        ttk.Entry(frame, textvariable=self.v["folder"]).pack(side="left", fill="x", expand=True)
        ttk.Button(frame, text="Browse…", command=self.browse_backup).pack(side="left", padx=4)
        ttk.Button(frame, text="Detect", command=self.refresh_candidates).pack(side="left")
        frame = ttk.Frame(self.body)
        frame.pack(fill="x", pady=(8, 0))
        self.cand_list = tk.Listbox(frame, width=1, height=4, exportselection=False)
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.cand_list.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.cand_list.xview)
        self.cand_list.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.cand_list.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)
        self.cand_error = self.wrapped_label()
        self.cand_error.pack(anchor="w", fill="x", pady=(0, 8))
        self.refresh_candidates()
        self.row("Certify key passphrase", lambda f: ttk.Entry(f, textvariable=self.v["passphrase"], show="*"))
        frame = ttk.Frame(self.body)
        frame.pack(fill="x", pady=(8, 0))
        ttk.Label(frame, text="Work folder (optional)", width=26).pack(side="left")
        ttk.Entry(frame, textvariable=self.v["workdir"]).pack(side="left", fill="x", expand=True)
        ttk.Button(frame, text="Browse…", command=lambda: self.v["workdir"].set(
            filedialog.askdirectory(parent=self.root, mustexist=True) or self.v["workdir"].get())).pack(
            side="left", padx=4)
        self.workdir_note = self.wrapped_label()
        self.workdir_note.pack(anchor="w", fill="x")
        self.update_workdir_note()

    def update_workdir_note(self):
        if self.workdir_note is None:
            return
        text = self.v["workdir"].get().strip()
        if text and not Path(text).is_dir():
            note = "This folder does not exist."
        else:
            base = Path(text) if text else osdeps.default_work_base()
            if osdeps.memory_backed(base):
                note = f"Temporary key copies go to {base} (memory-backed)."
            elif text:
                note = f"Work folder {base} is not a RAM disk; make sure it is an encrypted volume."
            else:
                note = (f"Temporary key copies go to {base}, which is on disk. Choose a RAM disk or an "
                        "encrypted volume to avoid that.")
        try:
            if self.workdir_note.winfo_exists():
                self.workdir_note.config(text=note)
        except tk.TclError:  # interpreter already gone
            pass

    def browse_backup(self):
        folder = filedialog.askdirectory(parent=self.root, mustexist=True)
        if folder:
            self.v["folder"].set(folder)
            self.refresh_candidates()

    def refresh_candidates(self):
        self.cand_list.delete(0, "end")
        self.cand_error.configure(text="")
        self.candidates = []
        folder = self.v["folder"].get()
        if not folder:
            return
        try:
            self.candidates = backup_mod.detect(Path(folder))
        except RecoveryError as err:
            self.cand_error.configure(text=gui_wording(err))
            return
        for c in self.candidates:
            self.cand_list.insert("end", f"{c.kind}: {c.path}")
        if len(self.candidates) == 1:
            self.cand_list.selection_set(0)

    def leave_backup(self):
        chosen = self.cand_list.curselection()
        if not chosen or chosen[0] >= len(self.candidates):
            self.alert("Select a detected backup.")
            return False
        candidate = self.candidates[chosen[0]]
        text = self.v["workdir"].get().strip()
        workdir = Path(text) if text else None
        s = self.session
        if (s and s.passphrase and s.bundle and (s.opts.backup, s.opts.source) == (candidate.path, candidate.kind)
                and s.opts.workdir == workdir):
            self.v["passphrase"].set("")
            return True  # already loaded and unlocked (the user went Back): keep it
        new = Session(Options(backup=candidate.path, source=candidate.kind, workdir=workdir), self.ui)
        try:
            new.validate_workdir(apply=False)
        except RecoveryError as err:
            self.alert(gui_wording(err))
            return False
        if self.session:
            self.session.cleanup()
        self.session = new
        new.validate_workdir()  # adopted: temporary homes now go to its work folder
        if workdir is not None:
            self.warn_leftovers()
        while True:
            try:
                self.session.load()
                break
            except backup_mod.AmbiguousKey as err:
                chosen = self.choose_key(err.bundles)
                if chosen is None:
                    return False
                self.session.opts.key = err.bundles[chosen].key_fp
            except RecoveryError as err:
                self.alert(gui_wording(err))
                return False
        if not self.session.unlock(self.v["passphrase"].get()):
            self.failures += 1
            if self.failures >= 3:
                self.alert("Wrong passphrase three times.")
                self.close()
            else:
                self.alert("Wrong passphrase.")
            return False
        self.v["passphrase"].set("")
        return True

    def warn_leftovers(self):
        """Preflight checked the default work folder; a chosen one may hold leftovers of its own."""
        warnings = self.session.leftover_warnings()
        if warnings:
            TextDialog(self.root, "Work folder", "Earlier runs left temporary folders in the work folder. "
                       "They may hold key material.", "\n".join(warnings), (("OK", None),), lines=6).run()

    def choose_key(self, bundles):
        return ChoiceDialog(self.root, TITLE, "This backup holds several keys. Choose the one to write to the cards.",
                            [f"{b.key_fp}  {', '.join(b.uids)}" for b in bundles]).run()

    def build_key(self):
        self.heading("3. Confirm key", "The subkeys marked <- card will be written to every new card.")
        self.readonly_text(self.session.describe(), 8)

    def build_mode(self):
        self.heading("4. Mode", MODE_HELP)
        for mode in ("clone", "renew", "rotate"):
            button = ttk.Radiobutton(self.body, text=mode, value=mode, variable=self.v["mode"])
            button.pack(anchor="w")
            if mode != "clone" and not self.session.bundle.has_certify_secret:
                button.state(["disabled"])
        self.row("Expiry (renew, rotate)", lambda f: ttk.Entry(f, textvariable=self.v["expire"]))
        self.row("Algorithm (rotate)", lambda f: ttk.Combobox(
            f, textvariable=self.v["algo"], values=("", "rsa4096", "ed25519"), state="readonly"))
        ttk.Checkbutton(self.body, text="Revoke the old subkeys (rotate)", variable=self.v["revoke"]).pack(anchor="w")

    def leave_mode(self):
        o = self.session.opts
        o.mode = self.v["mode"].get()
        o.expire = self.v["expire"].get().strip() or "2y"
        o.algo = self.v["algo"].get() if o.mode == "rotate" else ""
        o.revoke_old = self.v["revoke"].get() and o.mode == "rotate"
        try:
            self.session.validate_bundle()
            return True
        except RecoveryError as err:
            self.alert(gui_wording(err))
            return False

    def build_settings(self):
        self.heading("5. Card settings", METADATA_WARNING)
        self.row("Number of cards", lambda f: ttk.Spinbox(f, from_=1, to=10, textvariable=self.v["cards"], width=5))
        pins = ttk.Frame(self.body)
        pins.pack(fill="x", pady=2)
        ttk.Label(pins, text="PINs", width=26).pack(side="left")
        ttk.Radiobutton(pins, text="generate", value="generate", variable=self.v["pins"]).pack(side="left")
        ttk.Radiobutton(pins, text="enter", value="enter", variable=self.v["pins"]).pack(side="left", padx=(8, 0))
        self.row("", lambda f: ttk.Checkbutton(f, text="same PINs on every card", variable=self.v["same"]))
        self.row("Retries (user,reset,admin)", lambda f: ttk.Entry(f, textvariable=self.v["retries"]))
        for ref, label in (("sig", "Touch: sign"), ("dec", "Touch: decrypt"), ("aut", "Touch: authenticate")):
            self.row(label, lambda f, ref=ref: ttk.Combobox(f, textvariable=self.v[ref], values=TOUCH_POLICIES,
                                                            state="readonly"))
        self.row("Cardholder name", lambda f: ttk.Entry(f, textvariable=self.v["name"]))
        self.row("Login (blank = random)", lambda f: ttk.Entry(f, textvariable=self.v["login"]))
        self.row("Public key URL", lambda f: ttk.Entry(f, textvariable=self.v["url"]))

    def leave_settings(self):
        o = self.session.opts
        cards = self.v["cards"].get().strip()
        retries = tuple(part.strip() for part in self.v["retries"].get().split(","))
        name, login, url = (self.v[k].get().strip() for k in ("name", "login", "url"))
        problems = []
        if not cards.isdigit() or int(cards) < 1:
            problems.append("Number of cards must be a whole number, 1 or more.")
        if not valid_retries(retries):
            problems.append("Retries must be three numbers from 1 to 99, such as 5,5,5.")
        for label, value, limit in (("Cardholder name", name, 39), ("Login", login, 254), ("Public key URL", url, 254)):
            if not valid_metadata(value, limit):
                problems.append(f"{label} must be at most {limit} plain ASCII characters, without '<'.")
        if problems:
            self.alert("\n".join(problems))
            return False
        o.cards, o.retries = int(cards), retries
        o.pins, o.same_pins = self.v["pins"].get(), self.v["same"].get()
        o.touch = {ref: self.v[ref].get() for ref in ("sig", "dec", "aut")}
        o.name, o.login, o.url = name, login, url
        return True

    def build_output(self):
        self.heading("6. Output folder", "New backups (renew, rotate), the public key and the SSH key are written "
                                         "here. Use an empty folder outside the backup folder.")
        frame = ttk.Frame(self.body)
        frame.pack(fill="x")
        ttk.Entry(frame, textvariable=self.v["out"]).pack(side="left", fill="x", expand=True)
        ttk.Button(frame, text="Browse…", command=lambda: self.v["out"].set(
            filedialog.askdirectory(parent=self.root) or self.v["out"].get())).pack(side="left", padx=4)

    def leave_output(self):
        o = self.session.opts
        text = self.v["out"].get().strip()
        if not text:
            self.alert("Choose an output folder.")
            return False
        o.out = Path(text)
        try:
            self.session.validate_options()
            return True
        except RecoveryError as err:
            self.alert(gui_wording(err))
            return False

    def build_cards(self):
        self.heading("7. Write cards", "Press Start. You will be asked to insert each card and confirm its reset.")
        self.start_btn = ttk.Button(self.body, text="Start", command=self.start)
        self.start_btn.pack(side="bottom", anchor="e")  # before the log, which takes the remaining height
        frame = ttk.Frame(self.body)
        frame.pack(fill="x")
        cards = self.session.opts.cards
        self.table = ttk.Treeview(frame, columns=("status",), height=min(max(cards, 2), 4))
        self.table.heading("#0", text="Card")
        self.table.heading("status", text="Status")
        self.table.column("#0", width=self.em * 8, stretch=False)
        self.table.column("status", width=self.em * 20, stretch=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="x", expand=True)
        scroll.pack(side="left", fill="y")
        for i in range(1, cards + 1):
            self.table.insert("", "end", iid=str(i), text=f"card {i}", values=("waiting",))
        frame, self.logbox = scrolled_text(self.body, 6)
        frame.pack(fill="both", expand=True, pady=8)

    def start(self):
        self.start_btn.state(["disabled"])
        self.back_btn.state(["disabled"])
        self.worker = threading.Thread(target=self.work, daemon=True)
        self.worker.start()

    def work(self):
        code = EXIT_CARD
        try:
            code = self.session.execute()
        except RecoveryError as err:
            self.ui.error(str(err))
            code = err.code
        except Aborted:
            self.ui.error("aborted")
            code = EXIT_INTERRUPT
        except Exception as err:
            self.ui.error(f"{type(err).__name__}: {err}")
            code = EXIT_CARD
        finally:
            self.post(lambda: self.finished(code))

    def finished(self, code):
        self.log(f"finished with exit code {code}")
        self.next_btn.state(["!disabled"])
        self.reveal_pins()  # right away, so closing the window cannot lose them

    def build_summary(self):
        self.heading("8. Summary")
        o = self.session.opts
        lines = []
        for r in self.session.results:
            lines.append(f"card {r.index}  {r.serial or '-'}  {r.status}")
            if r.detail:
                lines.append(f"    {r.detail}")
        # The command stays one line, so it can be copied; the text widget wraps it for display.
        lines += ["", f"Files are in {o.out}.", "", "On your daily machine run:",
                  f"  {update_stubs_command()} --import <public key file> --remove-serial <old serial> "
                  f"--cards {o.cards}"]
        if update_stubs_where():
            lines.append(update_stubs_where())
        if self.pins:
            self.pin_btn = ttk.Button(self.body, text="Show PINs (once)", command=self.reveal_pins)
            self.pin_btn.pack(side="bottom", anchor="e", pady=8)
        else:
            note = "The PINs were shown when card writing finished." if self.pins_shown else "No card was written."
            ttk.Label(self.body, text=note).pack(side="bottom", anchor="e", pady=8)
        self.summary = self.readonly_text("\n".join(lines), 8)

    def reveal_pins(self):
        """Shows the kept PINs once in a modal dialog; nothing happens when there are none."""
        if not self.pins:
            return
        pins, self.pins = self.pins, []  # cleared first: a nested call cannot open a second dialog
        self.pins_dialog(pins).run()
        self.pins_shown = True
        btn = getattr(self, "pin_btn", None)
        if btn is not None and btn.winfo_exists():
            btn.state(["disabled"])

    def close(self):
        if self.root.grab_current() is not None:  # a dialog is up (the WM close button bypasses its grab)
            self.root.bell()
            return
        busy = self.worker is not None and self.worker.is_alive()
        if busy:
            messagebox.showinfo(TITLE, "Card writing is in progress. Finish it, or choose Cancel at the next "
                                       "card prompt to abort; PINs are shown afterwards.", parent=self.root)
            return
        self.reveal_pins()
        if self.session:
            self.session.cleanup()
        self.root.destroy()

    def report_error(self, kind, value, tb):
        """Tk callback errors: the windowed executable has no console, so log them and show a dialog."""
        if sys.stderr is not None:
            traceback.print_exception(kind, value, tb)
        try:
            self.log(f"error: {kind.__name__}: {value}")
            messagebox.showerror(TITLE, f"Unexpected error: {kind.__name__}: {value}", parent=self.root)
        except tk.TclError:  # the window is already gone
            pass

    def request_terminate(self, signum, frame):
        """SIGTERM/SIGHUP/SIGBREAK handler: only sets a flag, which poll() acts on inside the Tk loop."""
        self.terminate_requested = True

    def terminate(self):
        """Quit for a signal, even mid-run: reveal every PIN already changed, clean up, close."""
        if self.worker is not None and self.worker.is_alive() and self.session:
            self.pins = [(r.serial, r.admin_pin, r.user_pin) for r in self.session.results if r.pins_set]
        self.reveal_pins()
        if self.session:
            self.session.cleanup()
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    app = App(root)
    root.report_callback_exception = app.report_error
    for sig in TERMINATION_SIGNALS:
        signal.signal(sig, app.request_terminate)
    root.mainloop()
    return 0
