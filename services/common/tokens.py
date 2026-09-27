"""Shared handling of saved access tokens, for every provider."""

import os


class MissingToken(Exception):
    """No approval saved for this account and tool."""


class ExpiredToken(MissingToken):
    """An approval exists but no longer works; the user must grant again."""


def save_private(path, text):
    """Write a file readable only by its owner (on macOS and Linux)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
