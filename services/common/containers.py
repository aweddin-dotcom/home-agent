"""Commands that must run inside a container (they use the database in the
Docker volume) hand themselves off when started on the host."""

import os
import subprocess
import sys
from pathlib import Path

from . import settings


def in_container():
    return Path("/.dockerenv").exists() or os.environ.get("HOME_AGENT_IN_CONTAINER") == "1"


def delegate_to_container(service, module, argv=None):
    """If running on the host, run `python -m module args` in the service's
    container instead, and exit with its result. Inside a container, return."""
    if in_container():
        return
    args = sys.argv[1:] if argv is None else list(argv)
    print(f"(running in the {service} container)", flush=True)
    result = subprocess.run(
        ["docker", "compose", "exec", service, "python", "-m", module, *args], cwd=settings.ROOT
    )
    raise SystemExit(result.returncode)
