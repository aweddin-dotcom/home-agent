"""Paths, service URLs, and config shared by all services.

Defaults suit running on the host. Containers override them with
environment variables (e.g. OLLAMA_BASE_URL=http://host.docker.internal:11434).
"""

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("HOME_AGENT_DATA", ROOT / "data"))
CONFIG_DIR = Path(os.environ.get("HOME_AGENT_CONFIG", ROOT / "config"))
TOKENS_DIR = Path(os.environ.get("HOME_AGENT_TOKENS", DATA_DIR / "tokens"))
STRUCTURED_DB = DATA_DIR / "structured.db"

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://127.0.0.1:6333")
MODEL_PROFILE = os.environ.get("MODEL_PROFILE", "laptop")


def load_config(name):
    with (CONFIG_DIR / name).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def models():
    """Model names for the active profile, plus settings shared by all profiles."""
    config = load_config("models.yaml")
    return {**config["profiles"][MODEL_PROFILE], **config.get("shared", {})}


def retrieval():
    return load_config("retrieval_settings.yaml")
