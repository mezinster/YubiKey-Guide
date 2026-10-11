"""python3 -m ykrecover → GUI; --cli … → terminal; --ykman … → the bundled ykman CLI (Windows build)."""

import sys

UNSUPPORTED = ("ykrecover runs on Linux or Windows only (on Windows, install Gpg4win first). "
               "See recovery/README.md.")


def run_ykman(args: list) -> int:
    """Run ykman's own command line, as if the bundled copy were installed as `ykman`."""
    sys.argv = ["ykman", *args]
    from ykman._cli.__main__ import main as ykman_main
    return ykman_main()


CONSOLE_ONLY = "use ykrecover.exe for the command line"


def console_only_message() -> None:
    """ykrecover-gui.exe has no console: say so in a dialog instead of printing to nowhere."""
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showerror("YubiKey recovery", CONSOLE_ONLY, parent=root)
    finally:
        root.destroy()


def main() -> int:
    args = sys.argv[1:]
    if args[:1] in (["--cli"], ["--ykman"]) and getattr(sys, "frozen", False) and sys.stdout is None:
        console_only_message()
        return 1
    if args[:1] == ["--ykman"]:
        return run_ykman(args[1:])
    from . import osdeps
    if not osdeps.supported():
        print(f"error: {UNSUPPORTED}", file=sys.stderr)
        return 1
    if args[:1] == ["--cli"]:
        from .cli import main as cli_main
        return cli_main(args[1:])
    from .gui import main as gui_main
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
