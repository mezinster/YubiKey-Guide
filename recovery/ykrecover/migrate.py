"""One recovery run: preflight, backup, mode, cards, finish.

The CLI (run) and the GUI drive the same Session methods.
"""

import datetime
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import EXIT_BACKUP, EXIT_CARD, EXIT_INTERRUPT, EXIT_OK, EXIT_USAGE, Aborted, RecoveryError
from . import backup, card, modes
from .gpg import Home, base_is_tmpfs, gpg_version, runtime_base
from .plan import CARDHOME, STAGING, VERIFYHOME, format_plan, public_tokens, substitute


@dataclass
class Options:
    backup: Path
    mode: str = "clone"
    cards: int = 1
    out: Path = None
    expire: str = "2y"
    algo: str = ""
    revoke_old: bool = False
    pins: str = "generate"
    same_pins: bool = False
    retries: tuple = ("5", "5", "5")
    touch: dict = field(default_factory=lambda: {"sig": "on", "dec": "on", "aut": "on"})
    name: str = ""
    login: str = ""
    url: str = ""
    source: str = ""
    key: str = ""
    dry_run: bool = False
    yes: bool = False


@dataclass
class CardResult:
    index: int
    serial: str = ""
    status: str = "pending"  # OK, FAILED or SKIPPED once decided
    detail: str = ""
    admin_pin: str = ""
    user_pin: str = ""


class StepError(Exception):
    def __init__(self, shown: str, stderr: str):
        super().__init__(f"{shown}\n{stderr.strip()[-2000:]}")
        self.shown = shown


def network_interfaces_up() -> list:
    up = []
    for iface in sorted(Path("/sys/class/net").glob("*")):
        try:
            state = (iface / "operstate").read_text().strip()
        except OSError:
            continue
        if iface.name != "lo" and state == "up":
            up.append(iface.name)
    return up


class Session:
    def __init__(self, opts: Options, ui):
        self.opts, self.ui = opts, ui
        self.staging = None
        self.bundle = None
        self.targets = {}
        self.passphrase = ""
        self.results = []
        self.logins = {}

    def validate_options(self) -> None:
        o = self.opts
        if o.mode not in ("clone", "renew", "rotate"):
            raise RecoveryError(f"unknown mode {o.mode}", EXIT_USAGE)
        if o.cards < 1:
            raise RecoveryError("--cards must be 1 or more", EXIT_USAGE)
        if o.algo and o.algo not in modes.ALGOS:
            raise RecoveryError(f"unknown algorithm {o.algo}", EXIT_USAGE)
        if (o.algo or o.revoke_old) and o.mode != "rotate":
            raise RecoveryError("--algo and --revoke-old need --mode rotate", EXIT_USAGE)
        if o.source and o.source not in backup.KINDS:
            raise RecoveryError(f"unknown source {o.source}", EXIT_USAGE)
        if len(o.retries) != 3 or not all(r.isdigit() and 1 <= int(r) <= 99 for r in o.retries):
            raise RecoveryError("retries must be three numbers from 1 to 99", EXIT_USAGE)
        if any(p not in card.TOUCH_POLICIES for p in o.touch.values()):
            raise RecoveryError("unknown touch policy", EXIT_USAGE)
        if o.dry_run:
            return
        if o.out is None:
            raise RecoveryError("--out is required", EXIT_USAGE)
        out, src = o.out.resolve(), o.backup.resolve()
        if out == src or src in out.parents:
            raise RecoveryError("--out must be outside the backup folder", EXIT_USAGE)
        if out.is_dir() and any(out.glob("secret-*.key")):
            raise RecoveryError(f"{out} already holds key files; choose an empty folder", EXIT_USAGE)

    def preflight(self) -> None:
        tools = ["gpg", "gpgconf"] if self.opts.dry_run else ["gpg", "gpgconf", "gpg-connect-agent", "ykman"]
        missing = [t for t in tools if shutil.which(t) is None]
        if missing:
            raise RecoveryError(f"missing tools: {' '.join(missing)}", EXIT_USAGE)
        if gpg_version() < (2, 2):
            raise RecoveryError("gpg 2.2 or newer is required", EXIT_USAGE)
        if self.opts.dry_run:
            return
        for iface in network_interfaces_up():
            self.ui.warn(f"network interface {iface} is up; an air-gapped system is recommended")
        if not base_is_tmpfs(runtime_base()):
            self.ui.warn(f"{runtime_base()} is not tmpfs; key material may touch disk")

    def load(self) -> backup.KeyBundle:
        candidate = backup.choose(backup.detect(self.opts.backup), self.opts.source or None)
        if self.staging:
            self.staging.destroy()
        self.staging = Home.create("staging")
        self.bundle = backup.choose_key(backup.load(candidate, self.staging), self.opts.key or None)
        self.targets = modes.select_targets(self.bundle)
        return self.bundle

    def validate_bundle(self) -> None:
        mode = self.opts.mode
        if mode != "clone" and not self.bundle.has_certify_secret:
            raise RecoveryError(f"--mode {mode} needs the Certify key, but this backup only has subkeys",
                                EXIT_BACKUP)
        stale = modes.expired(self.bundle, self.targets, int(time.time()))
        if stale and mode == "clone":
            raise RecoveryError(f"subkeys have expired: {' '.join(stale)}; use --mode renew or --mode rotate",
                                EXIT_BACKUP)

    def describe(self) -> str:
        b = self.bundle
        lines = [f"Key {b.key_fp}", *(f"  uid {u}" for u in b.uids)]
        for k in b.subkeys:
            mark = " <- card" if k.fpr in self.targets.values() else ""
            expires = datetime.date.fromtimestamp(k.expires).isoformat() if k.expires else "never"
            lines.append(f"  [{k.usage.upper()}] {k.algo} {k.fpr} expires {expires}{mark}")
        return "\n".join(lines)

    def unlock(self, passphrase: str) -> bool:
        if backup.check_passphrase(self.staging, self.bundle, passphrase):
            self.passphrase = passphrase
            return True
        return False

    def _settings(self) -> card.CardSettings:
        o = self.opts
        return card.CardSettings(retries=tuple(o.retries), touch=dict(o.touch), name=o.name, url=o.url)

    def staging_steps(self) -> list:
        o, b = self.opts, self.bundle
        algo = o.algo or modes.default_algo(b, self.targets)
        return modes.mode_steps(o.mode, b, self.targets, o.expire, algo, o.revoke_old) + modes.backup_steps(o.mode, b)

    def card_plan(self, index: int, targets: dict) -> list:
        fp = self.bundle.key_fp
        return card.card_steps(index, fp, targets, self._settings()) + card.verify_steps(index, fp, targets)

    def _tokens(self) -> dict:
        return {
            "<CERTIFY_PASS>": self.passphrase,
            "<OUT>": str(self.opts.out),
            "<DATE>": datetime.date.today().isoformat(),
            "<DEFAULT_ADMIN_PIN>": card.DEFAULT_ADMIN_PIN,
            "<DEFAULT_USER_PIN>": card.DEFAULT_USER_PIN,
        }

    def _exec(self, step, homes: dict, tokens: dict) -> str:
        shown = substitute(" ".join(step.argv), public_tokens(tokens))
        argv = [substitute(arg, tokens) for arg in step.argv]
        stdin = [substitute(line, tokens) for line in step.stdin]
        result = homes[step.home].run(argv, stdin)
        if result.returncode != 0:
            raise StepError(shown, result.stderr)
        return result.stdout

    def apply_mode(self) -> None:
        tokens, homes = self._tokens(), {STAGING: self.staging}
        for step in self.staging_steps():
            try:
                self._exec(step, homes, tokens)
            except StepError as err:
                raise RecoveryError(f"{self.opts.mode} failed: {err}", EXIT_BACKUP) from err
        if self.opts.mode == "clone":
            return
        self.bundle = backup.choose_key(backup.list_bundles(self.staging), self.bundle.key_fp)
        self.targets = modes.select_targets(self.bundle)
        self.staging.kill_agents()

    def execute(self) -> int:
        """Everything after the passphrase is unlocked and the key confirmed."""
        o = self.opts
        o.out.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.apply_mode()
        if o.mode != "clone":
            while not self.ui.confirm(f"New backup written to {o.out}. Saved it to offline storage?"):
                self.ui.warn("copy the new backup before writing cards")
        self.assign_pins()
        self.write_cards()
        return self.finish()

    def assign_pins(self) -> None:
        o = self.opts
        self.results = [CardResult(i) for i in range(1, o.cards + 1)]
        shared = None
        for r in self.results:
            if not (o.same_pins and shared):
                if o.pins == "generate":
                    shared = card.generate_pins()
                else:
                    label = "all cards" if o.same_pins else f"card {r.index}"
                    shared = (self._ask_pin(f"Admin PIN ({label})", 8), self._ask_pin(f"User PIN ({label})", 6))
            r.admin_pin, r.user_pin = shared
            self.logins[r.index] = o.login or card.random_login()

    def _ask_pin(self, label: str, minimum: int) -> str:
        while True:
            pin = self.ui.ask_secret(f"{label}:")
            if not minimum <= len(pin) <= 127:
                self.ui.warn(f"must be {minimum} to 127 characters")
                continue
            if self.ui.ask_secret(f"Repeat {label}:") != pin:
                self.ui.warn("entries differ")
                continue
            return pin

    def write_cards(self) -> None:
        # Release the card from the user's own scdaemon so ykman can open it.
        subprocess.run(["gpgconf", "--kill", "scdaemon"], capture_output=True, check=False)
        done = set()
        for r in self.results:
            r.serial = self._wait_for_card(r.index, done)
            done.add(r.serial)
            if not self.ui.confirm_reset(r.serial):
                r.status, r.detail = "SKIPPED", "reset not confirmed"
                continue
            while True:
                try:
                    self._write_card(r)
                except StepError as err:
                    choice = self.ui.card_failed(r.index, r.serial, err.shown, str(err))
                    if choice == "retry":
                        continue
                    r.status, r.detail = "FAILED", err.shown
                    if choice == "abort":
                        raise Aborted() from err
                    break
                r.status, r.detail = "OK", ""
                self.ui.progress(r.index, f"{r.serial}: OK")
                break

    def _wait_for_card(self, index: int, done: set) -> str:
        while True:
            self.ui.wait_for_card(index, self.opts.cards)
            listing = subprocess.run(["ykman", "list", "--serials"], capture_output=True, text=True, check=False)
            serials = card.parse_serials(listing.stdout)
            if len(serials) != 1:
                self.ui.warn(f"expected exactly one YubiKey, found {len(serials)}")
            elif serials[0] in done:
                self.ui.warn(f"card {serials[0]} was already written in this session")
            else:
                return serials[0]

    def _write_card(self, r: CardResult) -> None:
        cardhome = self.staging.clone("card")
        verifyhome = Home.create("verify")
        try:
            (verifyhome.path / "test.txt").write_text(secrets.token_hex(32) + "\n")
            surname, given = card.split_name(self.opts.name)
            tokens = {**self._tokens(), f"<SERIAL{r.index}>": r.serial, "<ADMIN_PIN>": r.admin_pin,
                      "<USER_PIN>": r.user_pin, "<LOGIN>": self.logins[r.index], "<SURNAME>": surname,
                      "<GIVEN>": given, "<URL>": self.opts.url, "<CARDHOME>": str(cardhome.path),
                      "<VERIFYHOME>": str(verifyhome.path)}
            homes = {STAGING: self.staging, CARDHOME: cardhome, VERIFYHOME: verifyhome}
            for step in self.card_plan(r.index, self.targets):
                self.ui.progress(r.index, f"{r.serial}: {substitute(' '.join(step.argv), public_tokens(tokens))}")
                self._check(step, self._exec(step, homes, tokens), verifyhome)
        finally:
            cardhome.destroy()
            verifyhome.destroy()

    def _check(self, step, output: str, verifyhome: Home) -> None:
        if step.check == "cardstatus":
            want = [self.targets[u].upper() for u in modes.USAGES]
            got = card.parse_card_fprs(output)
            if got != want:
                raise StepError("card fingerprints", f"card has {got}, expected {want}")
        elif step.check == "roundtrip":
            if (verifyhome.path / "test.out").read_bytes() != (verifyhome.path / "test.txt").read_bytes():
                raise StepError("decrypt round-trip", "decrypted text differs from the original")

    def finish(self) -> int:
        b, o = self.bundle, self.opts
        for step in modes.finish_steps(b.key_fp, b.key_id, self.targets):
            try:
                self._exec(step, {STAGING: self.staging}, self._tokens())
            except StepError as err:
                self.ui.warn(f"export failed: {err}")
        self.ui.show_pins([(r.serial, r.admin_pin, r.user_pin) for r in self.results if r.status == "OK"])
        summary = [f"card {r.index} {r.serial or '-'} {r.status} {r.detail}".rstrip() for r in self.results]
        self.ui.info("Summary:\n" + "\n".join(summary))
        public = o.out / f"public-{b.key_id}-{datetime.date.today().isoformat()}.asc"
        self.ui.info(f"On your daily machine run: yubikey-recovery --cli update-stubs --import {public} "
                     f"--cards {o.cards}")
        return EXIT_OK if all(r.status == "OK" for r in self.results) else EXIT_CARD

    def dry_run_text(self) -> str:
        targets = modes.ROTATED if self.opts.mode == "rotate" else self.targets
        steps = self.staging_steps()
        for index in range(1, self.opts.cards + 1):
            steps += self.card_plan(index, targets)
        steps += modes.finish_steps(self.bundle.key_fp, self.bundle.key_id, targets)
        return format_plan(steps)

    def cleanup(self) -> None:
        if self.staging:
            self.staging.destroy()
            self.staging = None
        self.passphrase = ""


def run(opts: Options, ui) -> int:
    session = Session(opts, ui)
    try:
        session.validate_options()
        session.preflight()
        session.load()
        session.validate_bundle()
        if opts.dry_run:
            ui.output(session.dry_run_text())
            return EXIT_OK
        ui.info(session.describe())
        for _ in range(3):
            if session.unlock(ui.ask_secret("Certify key passphrase:")):
                break
            ui.warn("wrong passphrase")
        else:
            raise RecoveryError("wrong passphrase three times", EXIT_BACKUP)
        if not opts.yes and not ui.confirm(f"Write subkeys of {session.bundle.key_fp} to {opts.cards} card(s)?"):
            return EXIT_INTERRUPT
        return session.execute()
    except RecoveryError as err:
        ui.error(str(err))
        return err.code
    except (Aborted, KeyboardInterrupt):
        ui.error("aborted")
        return EXIT_INTERRUPT
    finally:
        session.cleanup()
