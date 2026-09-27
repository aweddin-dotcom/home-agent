"""Start the stack: export secrets from the OS credential store, then run
`docker compose up -d`. Extra arguments are passed to docker compose.

  python scripts/up.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

subprocess.run([sys.executable, str(ROOT / "scripts" / "secrets_cli.py"), "export"], check=True)
subprocess.run(["docker", "compose", "up", "-d", *sys.argv[1:]], cwd=ROOT, check=True)
