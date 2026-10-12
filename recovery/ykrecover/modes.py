"""clone / renew / rotate: which subkeys go to cards, and the staging steps that produce them."""

import datetime
import re

from . import EXIT_BACKUP, RecoveryError
from .plan import STAGING, Step

USAGES = ("s", "e", "a")
USAGE_WORD = {"s": "sign", "e": "encrypt", "a": "auth"}
SLOT_WORD = {"s": "sign", "e": "encrypt", "a": "authentication"}
ALGOS = {
    "rsa4096": {"s": "rsa4096", "e": "rsa4096", "a": "rsa4096"},
    "ed25519": {"s": "ed25519", "e": "cv25519", "a": "ed25519"},
}
ROTATED = {"s": "<NEW_FPR_S>", "e": "<NEW_FPR_E>", "a": "<NEW_FPR_A>"}
PASS = "<CERTIFY_PASS>"
LOOPBACK = ("gpg", "--batch", "--pinentry-mode", "loopback")
UNIT_SECONDS = {"": 86400, "d": 86400, "w": 7 * 86400, "m": 30 * 86400, "y": 365 * 86400}


def select_targets(bundle) -> dict:
    """The newest non-revoked subkey for each usage; None when the key has none."""
    targets = {}
    for usage in USAGES:
        usable = [k for k in bundle.subkeys if usage in k.usage and k.validity != "r"]
        targets[usage] = max(usable, key=lambda k: k.created).fpr if usable else None
    return targets


def unique(values) -> list:
    return list(dict.fromkeys(values))


def missing(targets) -> list:
    return [u for u in USAGES if targets[u] is None]


def present(targets) -> list:
    return unique(targets[u] for u in USAGES if targets[u] is not None)


def renew_targets(targets) -> dict:
    return {u: targets[u] or ROTATED[u] for u in USAGES}


def expired(bundle, targets, now: int) -> list:
    by_fpr = {k.fpr: k for k in bundle.subkeys}
    return [f for f in present(targets) if 0 < by_fpr[f].expires <= now]


def default_algo(bundle, targets) -> str:
    ref = targets["s"] or targets["e"] or targets["a"]
    algo = next((k.algo for k in bundle.subkeys if k.fpr == ref), bundle.algo)
    return "rsa4096" if algo.startswith("rsa") else "ed25519"


def expire_at(expire: str, now: int):
    """When subkeys given this --expire end; None for never, or for a form we do not parse."""
    text = expire.strip().lower()
    match = re.fullmatch(r"(\d+)([dwmy]?)", text)
    if match and int(match.group(1)) > 0:
        return now + int(match.group(1)) * UNIT_SECONDS[match.group(2)]
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        try:
            day = datetime.datetime(*map(int, match.groups()), tzinfo=datetime.timezone.utc)
        except ValueError:
            return None
        return int(day.timestamp())
    return None


def main_needs_extending(bundle, expire: str, now: int) -> bool:
    """A main key with an expiry that has passed, or that comes before the subkeys this run writes."""
    if bundle.expires <= 0:
        return False
    until = expire_at(expire, now)
    return bundle.expires <= now or until is None or bundle.expires < until


def mode_steps(mode, bundle, targets, expire, algo, revoke_old, now=0, unprotected=False) -> list:
    fp = bundle.key_fp
    steps = []
    if mode in ("renew", "rotate") and main_needs_extending(bundle, expire, now):
        steps.append(Step("mode", STAGING, (*LOOPBACK, "--passphrase-fd", "0", "--quick-set-expire", fp, expire),
                          (PASS,)))
    if mode == "renew":
        fprs = present(targets)
        if fprs:
            steps.append(Step("mode", STAGING, (*LOOPBACK, "--passphrase-fd", "0", "--quick-set-expire", fp, expire,
                                                *fprs), (PASS,)))
        steps += [Step("mode", STAGING, (*LOOPBACK, "--passphrase-fd", "0", "--quick-add-key", fp, ALGOS[algo][u],
                                         USAGE_WORD[u], expire), (PASS,)) for u in missing(targets)]
        return steps
    if mode != "rotate":
        return steps
    steps += [Step("mode", STAGING, (*LOOPBACK, "--passphrase-fd", "0", "--quick-add-key", fp, ALGOS[algo][u],
                                     USAGE_WORD[u], expire), (PASS,)) for u in USAGES]
    if revoke_old:
        for old in present(targets):
            steps.append(Step("mode", STAGING, ("gpgconf", "--kill", "all")))
            steps.append(Step("mode", STAGING, (*LOOPBACK, "--command-fd", "0", "--edit-key", fp),
                              (f"key {old}", "revkey", "y", "0", "", "y", PASS, "save")))
    return steps


def backup_steps(mode, bundle) -> list:
    """renew/rotate change key material, so a new backup is written before any card."""
    if mode == "clone":
        return []
    fp, kid = bundle.key_fp, bundle.key_id
    return [
        Step("backup", STAGING, (*LOOPBACK, "--passphrase-fd", "0", "--yes", "--armor", "--output",
                                 f"<OUT>/secret-{kid}-Certify.key", "--export-secret-keys", fp), (PASS,)),
        Step("backup", STAGING, (*LOOPBACK, "--passphrase-fd", "0", "--yes", "--armor", "--output",
                                 f"<OUT>/secret-{kid}-Subkeys.key", "--export-secret-subkeys", fp), (PASS,)),
        Step("backup", STAGING, ("gpg", "--batch", "--yes", "--armor", "--output",
                                 f"<OUT>/public-{kid}-<DATE>.asc", "--export", fp)),
    ]


def finish_steps(key_fp, key_id, targets) -> list:
    steps = [Step("finish", STAGING, ("gpg", "--batch", "--yes", "--armor", "--output",
                                      f"<OUT>/public-{key_id}-<DATE>.asc", "--export", key_fp))]
    if targets["a"]:
        steps.append(Step("finish", STAGING, ("gpg", "--batch", "--yes", "--output", f"<OUT>/ssh-{key_id}.pub",
                                              "--export-ssh-key", f"{targets['a']}!")))
    return steps
