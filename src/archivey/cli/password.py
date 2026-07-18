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
        return getpass.getpass("Password: ")

    return provider
