"""Paths, service URLs, and config shared by all services.

Defaults suit running on the host. Containers override them with
environment variables (e.g. OLLAMA_BASE_URL=http://host.docker.internal:11434).
"""

import os
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path):
    """Read KEY=value lines from .env without overriding real environment variables."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.environ.get("HOME_AGENT_DATA", ROOT / "data"))
CONFIG_DIR = Path(os.environ.get("HOME_AGENT_CONFIG", ROOT / "config"))
TOKENS_DIR = Path(os.environ.get("HOME_AGENT_TOKENS", DATA_DIR / "tokens"))
# In containers, the database lives in a Docker volume (HOME_AGENT_DB); see docker-compose.yml.
STRUCTURED_DB = Path(os.environ.get("HOME_AGENT_DB", DATA_DIR / "structured.db"))

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://127.0.0.1:6333")
MODEL_PROFILE = os.environ.get("MODEL_PROFILE", "laptop")
TIMEZONE = ZoneInfo(os.environ.get("HOME_AGENT_TIMEZONE", "UTC"))


def load_config(name):
    with (CONFIG_DIR / name).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def models():
    """Model names for the active profile, plus settings shared by all profiles."""
    config = load_config("models.yaml")
    return {**config["profiles"][MODEL_PROFILE], **config.get("shared", {})}


def retrieval():
    return load_config("retrieval_settings.yaml")


def digest():
    return load_config("digest.yaml")


PROFILE_PATH = Path(os.environ.get("HOME_AGENT_PROFILE", DATA_DIR / "profile" / "about-me.md"))


def profile_text(limit=4000):
    """The user's about-me notes, if written, without the template's comments.
    Personal data: only ever given to the local model."""
    import re

    if not PROFILE_PATH.is_file():
        return ""
    text = re.sub(r"<!--.*?-->", "", PROFILE_PATH.read_text(encoding="utf-8"), flags=re.DOTALL)
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:limit]


def accounts(include_disabled=False):
    """Accounts from config/accounts.yaml, keyed by label, in file order."""
    configured = load_config("accounts.yaml").get("accounts") or {}
    return {
        label: {"enabled": True, **(cfg or {})}
        for label, cfg in configured.items()
        if include_disabled or (cfg or {}).get("enabled", True)
    }
