"""Migrate GnuPG key backups onto new YubiKeys. See recovery/README.md."""

import signal

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_BACKUP = 2
EXIT_CARD = 3
EXIT_INTERRUPT = 130


def termination_signals(module=signal) -> tuple:
    """Signals that end a run like Ctrl-C. SIGHUP exists only on Linux, SIGBREAK only on Windows."""
    return tuple(getattr(module, name) for name in ("SIGTERM", "SIGHUP", "SIGBREAK") if hasattr(module, name))


TERMINATION_SIGNALS = termination_signals()


class RecoveryError(Exception):
    """A user-facing failure that ends the run with a specific exit code."""

    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


class Aborted(Exception):
    """The user cancelled, or stdin reached EOF at a prompt."""
