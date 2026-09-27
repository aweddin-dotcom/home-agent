"""Read a secret by name (config/secrets.yaml): from /run/secrets inside a
container, or from the OS credential store when running on the host."""

import os
from pathlib import Path

SECRETS_DIR = Path(os.environ.get("HOME_AGENT_SECRETS", "/run/secrets"))
SERVICE = "home-agent"


class MissingSecret(Exception):
    pass


def get_secret(name):
    path = SECRETS_DIR / name
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    try:
        import keyring  # host only; containers use /run/secrets

        value = keyring.get_password(SERVICE, name)
    except ImportError:
        value = None
    if not value:
        raise MissingSecret(f"Secret '{name}' is not set. Run: python scripts/secrets_cli.py set {name}")
    return value.strip()
