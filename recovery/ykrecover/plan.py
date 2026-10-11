"""The canonical step list shared by execution and --dry-run.

recoverCard.sh must print byte-identical plans; see tests/test_plan_parity.py.
"""

from dataclasses import dataclass

STAGING = "<STAGING>"
CARDHOME = "<CARDHOME>"
VERIFYHOME = "<VERIFYHOME>"
SECRET_TOKENS = ("<CERTIFY_PASS>", "<ADMIN_PIN>", "<USER_PIN>")


@dataclass(frozen=True)
class Step:
    phase: str
    home: str
    argv: tuple
    stdin: tuple = ()
    check: str = ""


def format_plan(steps) -> str:
    lines = []
    for number, step in enumerate(steps, start=1):
        lines.append(f"STEP {number} {step.phase} {step.home} {' '.join(step.argv)}")
        lines.extend(f"STDIN {line}" for line in step.stdin)
        if step.check:
            lines.append(f"CHECK {step.check}")
    return "\n".join(lines) + "\n"


def substitute(text: str, tokens: dict) -> str:
    for token, value in tokens.items():
        text = text.replace(token, value)
    return text


def public_tokens(tokens: dict) -> dict:
    """Tokens safe to show: secrets stay as placeholders."""
    return {k: v for k, v in tokens.items() if k not in SECRET_TOKENS}
