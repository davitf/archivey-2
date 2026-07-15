"""CLI-specific errors."""

from __future__ import annotations

from archivey.cli.exit_codes import EXIT_FAIL


class CliError(Exception):
    """User-facing CLI failure with an exit code."""

    def __init__(self, message: str, *, code: int = EXIT_FAIL) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
