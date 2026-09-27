"""Bring Home Agent up after login: wait for Docker, refresh the secret
files from the credential store, and start the containers. Installed by
scripts/autostart.py; runs without a window. Log: logs/startup.log.

Docker would restart the containers by itself, except after they were
stopped by hand (`docker compose down`); this covers that case too.
"""

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "startup.log"
DOCKER_TIMEOUT = 600  # seconds to wait for Docker Desktop after login
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flashes on Windows


def log(message):
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")


def run(command):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, creationflags=NO_WINDOW)
    return result.returncode, (result.stdout + result.stderr).strip()


def docker_ready():
    return run(["docker", "info"])[0] == 0


def main():
    LOG.parent.mkdir(exist_ok=True)
    LOG.write_text("", encoding="utf-8")  # keep only the latest start
    log("Waiting for Docker...")
    deadline = time.monotonic() + DOCKER_TIMEOUT
    while not docker_ready():
        if time.monotonic() > deadline:
            log(f"Docker didn't start within {DOCKER_TIMEOUT // 60} minutes; giving up. Is Docker Desktop running?")
            return 1
        time.sleep(5)

    (ROOT / "data" / "tokens").mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "structured.db").touch(exist_ok=True)
    code, output = run([sys.executable, str(ROOT / "scripts" / "secrets_cli.py"), "export"])
    log(f"Secrets: {output}")
    code, output = run(["docker", "compose", "up", "-d"])
    log("Containers started." if code == 0 else f"docker compose up failed ({code}):\n{output}")
    return code


if __name__ == "__main__":
    sys.exit(main())
