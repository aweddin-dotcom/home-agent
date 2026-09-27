"""Start the stack: export secrets from the OS credential store, then run
`docker compose up -d --build`. Extra arguments are passed to docker compose.

  python scripts/up.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# docker-compose.yml mounts this file; if it didn't exist, Docker would
# create a directory in its place. An empty file is a valid empty database.
db = ROOT / "data" / "structured.db"
db.parent.mkdir(parents=True, exist_ok=True)
db.touch(exist_ok=True)

subprocess.run([sys.executable, str(ROOT / "scripts" / "secrets_cli.py"), "export"], check=True)
subprocess.run(["docker", "compose", "up", "-d", "--build", *sys.argv[1:]], cwd=ROOT, check=True)
