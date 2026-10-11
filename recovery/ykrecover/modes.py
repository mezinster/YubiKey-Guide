"""clone / renew / rotate: which subkeys go to cards, and the staging steps that produce them."""

from . import EXIT_BACKUP, RecoveryError
from .plan import STAGING, Step

USAGES = ("s", "e", "a")
USAGE_WORD = {"s": "sign", "e": "encrypt", "a": "auth"}
ALGOS = {
    "rsa4096": {"s": "rsa4096", "e": "rsa4096", "a": "rsa4096"},
    "ed25519": {"s": "ed25519", "e": "cv25519", "a": "ed25519"},
}
ROTATED = {"s": "<NEW_FPR_S>", "e": "<NEW_FPR_E>", "a": "<NEW_FPR_A>"}
PASS = "<CERTIFY_PASS>"
LOOPBACK = ("gpg", "--batch", "--pinentry-mode", "loopback")


def select_targets(bundle) -> dict:
    """The newest non-revoked subkey for each usage."""
    targets = {}
    for usage in USAGES:
        usable = [k for k in bundle.subkeys if usage in k.usage and k.validity != "r"]
        if not usable:
            raise RecoveryError(f"key {bundle.key_fp} has no {USAGE_WORD[usage]} subkey", EXIT_BACKUP)
        targets[usage] = max(usable, key=lambda k: k.created).fpr
    return targets


def unique(values) -> list:
    return list(dict.fromkeys(values))


def expired(bundle, targets, now: int) -> list:
    by_fpr = {k.fpr: k for k in bundle.subkeys}
    return [f for f in unique(targets[u] for u in USAGES) if 0 < by_fpr[f].expires <= now]


def default_algo(bundle, targets) -> str:
    algo = next(k.algo for k in bundle.subkeys if k.fpr == targets["s"])
    return "rsa4096" if algo.startswith("rsa") else "ed25519"


def mode_steps(mode, bundle, targets, expire, algo, revoke_old) -> list:
    fp = bundle.key_fp
    if mode == "renew":
        fprs = unique(targets[u] for u in USAGES)
        return [Step("mode", STAGING, (*LOOPBACK, "--passphrase-fd", "0", "--quick-set-expire", fp, expire, *fprs),
                     (PASS,))]
    if mode != "rotate":
        return []
    steps = [Step("mode", STAGING, (*LOOPBACK, "--passphrase-fd", "0", "--quick-add-key", fp, ALGOS[algo][u],
                                    USAGE_WORD[u], expire), (PASS,)) for u in USAGES]
    if revoke_old:
        for old in unique(targets[u] for u in USAGES):
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
    return [
        Step("finish", STAGING, ("gpg", "--batch", "--yes", "--armor", "--output",
                                 f"<OUT>/public-{key_id}-<DATE>.asc", "--export", key_fp)),
        Step("finish", STAGING, ("gpg", "--batch", "--yes", "--output", f"<OUT>/ssh-{key_id}.pub",
                                 "--export-ssh-key", f"{targets['a']}!")),
    ]
