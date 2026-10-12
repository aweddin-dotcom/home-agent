"""Start the stack: export secrets from the OS credential store, then run
`docker compose up -d --build`. Extra arguments are passed to docker compose.
SearXNG (web search) is started only while config/web_search.yaml has it on.

  python scripts/up.py
"""

import os
import re
import secrets
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
web_config = (ROOT / "config" / "web_search.yaml").read_text(encoding="utf-8")
profiles = ["--profile", "web"] if re.search(r"^enabled:\s*true\b", web_config, re.M) else []
# SearXNG's key only signs its preference cookies, so a new one each start is fine.
env = {**os.environ, "SEARXNG_SECRET": secrets.token_hex(32)}
subprocess.run(["docker", "compose", *profiles, "up", "-d", "--build", *sys.argv[1:]], cwd=ROOT, env=env,
               check=True)
