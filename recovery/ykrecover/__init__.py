"""Migrate GnuPG key backups onto new YubiKeys. See recovery/README.md."""

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_BACKUP = 2
EXIT_CARD = 3
EXIT_INTERRUPT = 130


class RecoveryError(Exception):
    """A user-facing failure that ends the run with a specific exit code."""

    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


class Aborted(Exception):
    """The user cancelled, or stdin reached EOF at a prompt."""
