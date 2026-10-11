"""Tkinter wizard over migrate.Session.

Card work runs on a worker thread; every UI call it makes is queued to the Tk main loop,
which owns all widgets and dialogs.
"""

import queue
import signal
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import EXIT_CARD, EXIT_INTERRUPT, TERMINATION_SIGNALS, Aborted, RecoveryError, osdeps
from . import backup as backup_mod
from .card import TOUCH_POLICIES
from .migrate import Options, Session

TITLE = "YubiKey recovery"
METADATA_WARNING = ("OpenPGP card metadata (name, login, URL) can be read by anyone holding the card. "
                    "Leave the name blank unless you want it for lost-and-found.")
MODE_HELP = ("clone: write the backed-up subkeys as they are.\n"
             "renew: extend their expiry first (needs the Certify key).\n"
             "rotate: create new subkeys (needs the Certify key). Choose this if the old cards may be "
             "lost or compromised.")


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
        return bool(self._ask(lambda: messagebox.askyesno(TITLE, prompt, parent=self.app.root)))

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
        answer = self._ask(lambda: messagebox.askyesnocancel(
            "Card failed", f"Card {serial} failed at:\n{step}\n\n{error}\n\nYes: retry   No: skip   Cancel: abort",
            parent=self.app.root))
        return {True: "retry", False: "skip", None: "abort"}[answer]

    def progress(self, index, status):
        self.app.post(lambda: self.app.set_card(index, status))

    def show_pins(self, pins):
        self.app.post(lambda: self.app.keep_pins(pins))


class App:
    def __init__(self, root):
        self.root = root
        root.title(TITLE)
        root.minsize(760, 560)
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
        self.body = ttk.Frame(root, padding=12)
        self.body.pack(fill="both", expand=True)
        nav = ttk.Frame(root, padding=(12, 0, 12, 12))
        nav.pack(fill="x")
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
                reschedule = False  # the window is destroyed: a new "after" would outlive it
                self.terminate()
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
        self.back_btn.state(["!disabled"] if 0 < page < 6 else ["disabled"])
        self.next_btn.state(["!disabled"] if page < 6 else ["disabled"])
        self.pages[page][0]()

    def next(self):
        leave = self.pages[self.page][1]
        if leave is None or leave():
            self.show(self.page + 1)

    def heading(self, text, detail=""):
        ttk.Label(self.body, text=text, font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
        if detail:
            ttk.Label(self.body, text=detail, wraplength=700, justify="left").pack(anchor="w", pady=(4, 12))

    def row(self, label, make):
        frame = ttk.Frame(self.body)
        frame.pack(fill="x", pady=2)
        ttk.Label(frame, text=label, width=26).pack(side="left")
        widget = make(frame)
        widget.pack(side="left", fill="x", expand=True)
        return widget

    def readonly_text(self, content, height):
        text = tk.Text(self.body, height=height, wrap="none")
        text.insert("end", content)
        text.config(state="disabled")
        text.pack(fill="both", expand=True)

    def log(self, text):
        if self.logbox is not None and self.logbox.winfo_exists():
            self.logbox.insert("end", text.rstrip("\n") + "\n")
            self.logbox.see("end")

    def set_card(self, index, status):
        if self.table is not None and self.table.winfo_exists() and self.table.exists(str(index)):
            self.table.item(str(index), values=(status,))

    def keep_pins(self, pins):
        self.pins = list(pins)

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
        self.readonly_text("\n".join(lines), 8)

    def build_backup(self):
        self.heading("2. Backup", "Choose the folder holding your backup, for example the mounted encrypted "
                                  "USB volume. It is only read, never changed.")
        frame = ttk.Frame(self.body)
        frame.pack(fill="x")
        ttk.Entry(frame, textvariable=self.v["folder"]).pack(side="left", fill="x", expand=True)
        ttk.Button(frame, text="Browse…", command=self.browse_backup).pack(side="left", padx=4)
        ttk.Button(frame, text="Detect", command=self.refresh_candidates).pack(side="left")
        self.cand_list = tk.Listbox(self.body, height=6, exportselection=False)
        self.cand_list.pack(fill="x", pady=8)
        self.refresh_candidates()
        self.row("Certify key passphrase", lambda f: ttk.Entry(f, textvariable=self.v["passphrase"], show="*"))
        frame = ttk.Frame(self.body)
        frame.pack(fill="x", pady=(8, 0))
        ttk.Label(frame, text="Work folder (optional)", width=26).pack(side="left")
        ttk.Entry(frame, textvariable=self.v["workdir"]).pack(side="left", fill="x", expand=True)
        ttk.Button(frame, text="Browse…", command=lambda: self.v["workdir"].set(
            filedialog.askdirectory(parent=self.root, mustexist=True) or self.v["workdir"].get())).pack(
            side="left", padx=4)
        self.workdir_note = ttk.Label(self.body, wraplength=700, justify="left")
        self.workdir_note.pack(anchor="w")
        self.v["workdir"].trace_add("write", lambda *_: self.update_workdir_note())
        self.update_workdir_note()

    def update_workdir_note(self):
        text = self.v["workdir"].get().strip()
        if text and not Path(text).is_dir():
            note = "This folder does not exist."
        else:
            base = Path(text) if text else osdeps.work_base()
            note = (f"Temporary key copies go to {base} (memory-backed)." if osdeps.memory_backed(base) else
                    f"Temporary key copies go to {base}, which is on disk. Choose a RAM disk or an "
                    "encrypted volume to avoid that.")
        if self.workdir_note.winfo_exists():
            self.workdir_note.config(text=note)

    def browse_backup(self):
        folder = filedialog.askdirectory(parent=self.root, mustexist=True)
        if folder:
            self.v["folder"].set(folder)
            self.refresh_candidates()

    def refresh_candidates(self):
        self.cand_list.delete(0, "end")
        self.candidates = []
        folder = self.v["folder"].get()
        if not folder:
            return
        try:
            self.candidates = backup_mod.detect(Path(folder))
        except RecoveryError as err:
            self.cand_list.insert("end", str(err))
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
        if self.session:
            self.session.cleanup()
        self.session = Session(Options(backup=candidate.path, source=candidate.kind, workdir=workdir), self.ui)
        try:
            self.session.validate_workdir()
        except RecoveryError as err:
            self.alert(str(err))
            return False
        while True:
            try:
                self.session.load()
                break
            except backup_mod.AmbiguousKey as err:
                key = simpledialog.askstring(TITLE, "Several keys found:\n" + "\n".join(b.key_fp for b in err.bundles)
                                             + "\n\nEnter the fingerprint to use:", parent=self.root)
                if not key:
                    return False
                self.session.opts.key = key
            except RecoveryError as err:
                self.alert(str(err))
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

    def build_key(self):
        self.heading("3. Confirm key", "The subkeys marked <- card will be written to every new card.")
        self.readonly_text(self.session.describe(), 14)

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
            self.alert(str(err))
            return False

    def build_settings(self):
        self.heading("5. Card settings", METADATA_WARNING)
        self.row("Number of cards", lambda f: ttk.Spinbox(f, from_=1, to=10, textvariable=self.v["cards"], width=5))
        pins = ttk.Frame(self.body)
        pins.pack(fill="x", pady=2)
        ttk.Label(pins, text="PINs", width=26).pack(side="left")
        ttk.Radiobutton(pins, text="generate", value="generate", variable=self.v["pins"]).pack(side="left")
        ttk.Radiobutton(pins, text="enter", value="enter", variable=self.v["pins"]).pack(side="left")
        ttk.Checkbutton(pins, text="same PINs on every card", variable=self.v["same"]).pack(side="left", padx=12)
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
        o.cards = int(cards) if cards.isdigit() else 0
        o.pins, o.same_pins = self.v["pins"].get(), self.v["same"].get()
        o.retries = tuple(self.v["retries"].get().split(","))
        o.touch = {ref: self.v[ref].get() for ref in ("sig", "dec", "aut")}
        o.name, o.login, o.url = (self.v[k].get().strip() for k in ("name", "login", "url"))
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
        o.out = Path(self.v["out"].get()) if self.v["out"].get().strip() else None
        try:
            self.session.validate_options()
            return True
        except RecoveryError as err:
            self.alert(str(err))
            return False

    def build_cards(self):
        self.heading("7. Write cards", "Press Start. You will be asked to insert each card and confirm its reset.")
        self.table = ttk.Treeview(self.body, columns=("status",), height=6)
        self.table.heading("#0", text="Card")
        self.table.heading("status", text="Status")
        self.table.column("status", width=560)
        self.table.pack(fill="x")
        for i in range(1, self.session.opts.cards + 1):
            self.table.insert("", "end", iid=str(i), text=f"card {i}", values=("waiting",))
        self.logbox = tk.Text(self.body, height=12, wrap="word")
        self.logbox.pack(fill="both", expand=True, pady=8)
        self.start_btn = ttk.Button(self.body, text="Start", command=self.start)
        self.start_btn.pack(anchor="e")

    def start(self):
        self.start_btn.state(["disabled"])
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
        lines = [f"card {r.index}  {r.serial or '-'}  {r.status}  {r.detail}" for r in self.session.results]
        lines += ["", f"Files are in {o.out}.", "On your daily machine run:",
                  "  yubikey-recovery --cli update-stubs --import <public key file> --remove-serial <old serial> "
                  f"--cards {o.cards}"]
        self.readonly_text("\n".join(lines), 12)
        if self.pins:
            self.pin_btn = ttk.Button(self.body, text="Show PINs (once)", command=self.reveal_pins)
            self.pin_btn.pack(anchor="e", pady=8)
        else:
            note = "The PINs were shown when card writing finished." if self.pins_shown else "No card was written."
            ttk.Label(self.body, text=note).pack(anchor="e", pady=8)

    def reveal_pins(self):
        """Shows the kept PINs once in a modal dialog; nothing happens when there are none."""
        if not self.pins:
            return
        text = "\n".join(f"Card {s}: Admin PIN {a}   User PIN {u}" for s, a, u in self.pins)
        messagebox.showinfo("PINs", text + "\n\nWrite them down now. They are not shown again.", parent=self.root)
        self.pins = []
        self.pins_shown = True
        btn = getattr(self, "pin_btn", None)
        if btn is not None and btn.winfo_exists():
            btn.state(["disabled"])

    def close(self):
        busy = self.worker is not None and self.worker.is_alive()
        if busy:
            messagebox.showinfo(TITLE, "Card writing is in progress. Finish it, or choose Cancel at the next "
                                       "card prompt to abort; PINs are shown afterwards.", parent=self.root)
            return
        self.reveal_pins()
        if self.session:
            self.session.cleanup()
        self.root.destroy()

    def request_terminate(self, signum, frame):
        """SIGTERM/SIGHUP handler: only sets a flag, which poll() acts on inside the Tk loop."""
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
    for sig in TERMINATION_SIGNALS:
        signal.signal(sig, app.request_terminate)
    root.mainloop()
    return 0
