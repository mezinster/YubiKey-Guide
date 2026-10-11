"""Per-card write and verify steps, card discovery and card-status parsing."""

import secrets
import string
from dataclasses import dataclass, field

from .plan import CARDHOME, STAGING, VERIFYHOME, Step

DEFAULT_ADMIN_PIN = "12345678"
DEFAULT_USER_PIN = "123456"
SLOTS = (("s", "1", "sig"), ("e", "2", "dec"), ("a", "3", "aut"))
TOUCH_POLICIES = ("off", "on", "fixed", "cached", "cached-fixed")
CARD_GPG = ("gpg", "--batch", "--command-fd", "0", "--pinentry-mode", "loopback")


@dataclass(frozen=True)
class CardSettings:
    retries: tuple = ("5", "5", "5")
    touch: dict = field(default_factory=lambda: {"sig": "on", "dec": "on", "aut": "on"})
    name: str = ""
    url: str = ""


def generate_pins() -> tuple:
    """The guide's method: 8-digit Admin PIN, 6-digit User PIN."""
    digits = "".join(secrets.choice(string.digits) for _ in range(14))
    return digits[:8], digits[8:]


def random_login() -> str:
    """Non-identifying login, as the guide recommends."""
    return "yk." + "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(16))


def split_name(name: str) -> tuple:
    parts = name.split()
    return (parts[-1], " ".join(parts[:-1])) if parts else ("", "")


def card_steps(index: int, key_fp: str, targets: dict, settings: CardSettings) -> list:
    phase, serial = f"card{index}", f"<SERIAL{index}>"
    kill = Step(phase, CARDHOME, ("gpgconf", "--kill", "all"))
    holder = ["admin", "login", "<LOGIN>", "<ADMIN_PIN>"]
    if settings.name:
        holder += ["name", "<SURNAME>", "<GIVEN>"]
    if settings.url:
        holder += ["url", "<URL>"]
    steps = [
        kill, Step(phase, CARDHOME, ("ykman", "--device", serial, "openpgp", "reset", "-f")),
        # Retries first: changing the retry counters may reset the PINs to defaults.
        kill, Step(phase, CARDHOME, ("ykman", "--device", serial, "openpgp", "access", "set-retries",
                                     *settings.retries, "-f", "-a", "<DEFAULT_ADMIN_PIN>")),
        kill, Step(phase, CARDHOME, (*CARD_GPG, "--change-pin"),
                   ("3", "<DEFAULT_ADMIN_PIN>", "<ADMIN_PIN>", "<ADMIN_PIN>", "q")),
        kill, Step(phase, CARDHOME, (*CARD_GPG, "--change-pin"),
                   ("1", "<DEFAULT_USER_PIN>", "<USER_PIN>", "<USER_PIN>", "q")),
        kill, Step(phase, CARDHOME, (*CARD_GPG, "--edit-card"), (*holder, "quit")),
    ]
    for usage, slot, _ in SLOTS:
        steps += [kill, Step(phase, CARDHOME, (*CARD_GPG, "--edit-key", key_fp),
                             (f"key {targets[usage]}", "keytocard", slot, "<CERTIFY_PASS>", "<ADMIN_PIN>", "save"))]
    for _, _, ref in SLOTS:
        steps += [kill, Step(phase, CARDHOME, ("ykman", "--device", serial, "openpgp", "keys", "set-touch", ref,
                                               settings.touch[ref], "-f", "-a", "<ADMIN_PIN>"))]
    return steps


def verify_steps(index: int, key_fp: str, targets: dict) -> list:
    phase, v = f"verify{index}", VERIFYHOME
    pin = ("gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase-fd", "0", "--yes")
    return [
        Step(phase, STAGING, ("gpg", "--batch", "--yes", "--armor", "--output", f"{v}/pub.asc", "--export", key_fp)),
        Step(phase, v, ("gpg", "--batch", "--import", f"{v}/pub.asc")),
        Step(phase, v, ("gpg", "--batch", "--with-colons", "--card-status"), check="cardstatus"),
        Step(phase, v, (*pin, "--local-user", f"{targets['s']}!", "--output", f"{v}/test.sig", "--detach-sign",
                        f"{v}/test.txt"), ("<USER_PIN>",)),
        Step(phase, v, ("gpg", "--batch", "--verify", f"{v}/test.sig", f"{v}/test.txt")),
        Step(phase, v, ("gpg", "--batch", "--yes", "--trust-model", "always", "--recipient", f"{targets['e']}!",
                        "--output", f"{v}/test.gpg", "--encrypt", f"{v}/test.txt")),
        Step(phase, v, ("gpgconf", "--kill", "all")),
        Step(phase, v, (*pin, "--output", f"{v}/test.out", "--decrypt", f"{v}/test.gpg"), ("<USER_PIN>",),
             check="roundtrip"),
    ]


def parse_serials(text: str) -> list:
    return [line.strip() for line in text.splitlines() if line.strip().isdigit()]


def parse_card_fprs(colons: str) -> list:
    for line in colons.splitlines():
        if line.startswith("fpr:"):
            return [f.upper() for f in line.split(":")[1:4]]
    return ["", "", ""]
