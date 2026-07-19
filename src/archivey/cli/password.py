"""Password helpers for the CLI."""

from __future__ import annotations

import getpass
import sys

from archivey.config import PasswordInput, PasswordRequest


def resolve_password(cli_password: str | None) -> PasswordInput:
    """Return a password value or a TTY ``getpass`` provider when none was given."""
    if cli_password is not None:
        return cli_password

    def provider(_request: PasswordRequest) -> str | None:
        if not sys.stdin.isatty():
            return None
        try:
            return getpass.getpass("Password: ")
        except EOFError:
            # Ctrl-D / end-of-input at the prompt → treat as "no password given".
            return None

    return provider
