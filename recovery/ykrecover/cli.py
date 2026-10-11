"""Terminal front end: python3 -m ykrecover --cli [options] | --cli update-stubs [options]."""

import argparse
import getpass
import signal
import sys
from pathlib import Path

from . import EXIT_INTERRUPT, EXIT_USAGE, TERMINATION_SIGNALS, Aborted
from .card import TOUCH_POLICIES
from .migrate import Options, run


class CliUI:
    """Prompts on stderr, answers from stdin. Secrets use getpass only on a TTY."""

    def _line(self, prompt: str) -> str:
        sys.stderr.write(prompt + " ")
        sys.stderr.flush()
        line = sys.stdin.readline()
        if not line:
            sys.stderr.write("\n")
            raise Aborted()
        return line.rstrip("\n")

    def output(self, text):
        sys.stdout.write(text)

    def info(self, msg):
        print(msg, file=sys.stderr)

    def warn(self, msg):
        print(f"warning: {msg}", file=sys.stderr)

    def error(self, msg):
        print(f"error: {msg}", file=sys.stderr)

    def ask_secret(self, prompt):
        if sys.stdin.isatty():
            try:
                return getpass.getpass(prompt + " ")
            except EOFError as err:
                raise Aborted() from err
        return self._line(prompt)

    def confirm(self, prompt):
        return self._line(f"{prompt} [y/N]").lower() in ("y", "yes")

    def confirm_reset(self, serial):
        return self._line(f"Type RESET {serial} to erase card {serial}:") == f"RESET {serial}"

    def wait_for_card(self, index, total):
        self._line(f"Insert card {index} of {total} and press Enter")

    def card_failed(self, index, serial, step, error):
        self.warn(f"card {serial} failed at: {step}\n{error}")
        while True:
            answer = self._line("[r]etry, [s]kip or [a]bort?").lower()[:1]
            if answer in ("r", "s", "a"):
                return {"r": "retry", "s": "skip", "a": "abort"}[answer]

    def progress(self, index, status):
        print(f"[card {index}] {status}", file=sys.stderr)

    def show_pins(self, pins):
        for serial, admin, user in pins:
            print(f"Card {serial}: Admin PIN {admin}  User PIN {user}", file=sys.stderr)


def _retries(text: str) -> tuple:
    parts = tuple(text.split(","))
    if len(parts) != 3 or not all(p.isdigit() and 1 <= int(p) <= 99 for p in parts):
        raise argparse.ArgumentTypeError("expected U,R,A such as 5,5,5")
    return parts


def _positive(text: str) -> int:
    if not text.isdigit():
        raise argparse.ArgumentTypeError("expected a number")
    return int(text)


def migrate_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ykrecover --cli", description="Migrate GnuPG key backups to new YubiKeys.",
                                allow_abbrev=False)
    p.add_argument("--backup", type=Path, required=True)
    p.add_argument("--mode", choices=("clone", "renew", "rotate"), default="clone")
    p.add_argument("--cards", type=_positive, default=1)
    p.add_argument("--out", type=Path)
    p.add_argument("--expire", default="2y")
    p.add_argument("--algo", choices=("rsa4096", "ed25519"), default="")
    p.add_argument("--revoke-old", action="store_true")
    p.add_argument("--pins", choices=("generate", "enter"), default="generate")
    p.add_argument("--same-pins", action="store_true")
    p.add_argument("--retries", type=_retries, default=("5", "5", "5"))
    p.add_argument("--touch", choices=TOUCH_POLICIES, default="on")
    p.add_argument("--touch-sig", choices=TOUCH_POLICIES)
    p.add_argument("--touch-enc", choices=TOUCH_POLICIES)
    p.add_argument("--touch-aut", choices=TOUCH_POLICIES)
    p.add_argument("--name", default="")
    p.add_argument("--login", default="")
    p.add_argument("--url", default="")
    p.add_argument("--source", choices=("export", "subkeys-only", "gnupghome"), default="")
    p.add_argument("--key", default="")
    p.add_argument("--workdir", type=Path)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true")
    return p


def stubs_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ykrecover --cli update-stubs",
                                description="Point the daily keyring at the new cards.", allow_abbrev=False)
    p.add_argument("--import", dest="import_file", type=Path)
    p.add_argument("--remove-serial", action="append", default=[])
    p.add_argument("--cards", type=_positive, default=1)
    return p


def _parse(parser: argparse.ArgumentParser, argv: list):
    try:
        return parser.parse_args(argv), None
    except SystemExit as exit_:
        return None, (EXIT_USAGE if exit_.code else 0)


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def main(argv: list) -> int:
    """SIGTERM, SIGHUP and SIGBREAK end the run like Ctrl-C: cleanup runs and the PINs are shown."""
    previous = {sig: signal.signal(sig, _interrupt) for sig in TERMINATION_SIGNALS}
    try:
        return _main(argv)
    except KeyboardInterrupt:  # a second signal during cleanup; atexit still removes the homes
        return EXIT_INTERRUPT
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _main(argv: list) -> int:
    if argv[:1] == ["update-stubs"]:
        args, code = _parse(stubs_parser(), argv[1:])
        if args is None:
            return code
        from .stubs import update_stubs
        return update_stubs(CliUI(), args.import_file, args.remove_serial, args.cards)
    args, code = _parse(migrate_parser(), argv)
    if args is None:
        return code
    touch = {"sig": args.touch_sig or args.touch, "dec": args.touch_enc or args.touch,
             "aut": args.touch_aut or args.touch}
    opts = Options(backup=args.backup, mode=args.mode, cards=args.cards, out=args.out, expire=args.expire,
                   algo=args.algo, revoke_old=args.revoke_old, pins=args.pins, same_pins=args.same_pins,
                   retries=args.retries, touch=touch, name=args.name, login=args.login, url=args.url,
                   source=args.source, key=args.key, workdir=args.workdir, dry_run=args.dry_run, yes=args.yes)
    return run(opts, CliUI())
