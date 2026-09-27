"""Start the stack: export secrets from the OS credential store, then run
`docker compose up -d --build`. Extra arguments are passed to docker compose.

  python scripts/up.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# docker-compose.yml mounts this file (as the one-time seed for the sync
# worker's database); if it didn't exist, Docker would create a directory in
# its place. An empty file is simply skipped.
db = ROOT / "data" / "structured.db"
db.parent.mkdir(parents=True, exist_ok=True)
db.touch(exist_ok=True)
(ROOT / "data" / "tokens").mkdir(parents=True, exist_ok=True)  # mounted into sync-worker

subprocess.run([sys.executable, str(ROOT / "scripts" / "secrets_cli.py"), "export"], check=True)
subprocess.run(["docker", "compose", "up", "-d", "--build", *sys.argv[1:]], cwd=ROOT, check=True)
