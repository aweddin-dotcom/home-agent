"""Manage the project's secrets.

Values live in the OS credential store (Windows Credential Manager or the
macOS Keychain) via the keyring library. A value is only ever typed at a
hidden prompt or read from a file, never passed on the command line, so it
can't end up in shell history or logs.

  python scripts/secrets_cli.py status
  python scripts/secrets_cli.py set NAME [--from-file PATH]
  python scripts/secrets_cli.py remove NAME
  python scripts/secrets_cli.py export

`export` writes each secret to secrets/NAME, where docker-compose.yml hands
it to the services that need it at /run/secrets/NAME.
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

import keyring
import keyring.errors
import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "config" / "secrets.yaml"
OUT_DIR = ROOT / "secrets"
SERVICE = "home-agent"


def load_registry():
    with REGISTRY.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["secrets"]


def require_known(name, registry):
    if name not in registry:
        sys.exit(f"Unknown secret '{name}'. Add it to config/secrets.yaml first.")


def cmd_status(args, registry):
    for name, info in registry.items():
        state = "set" if keyring.get_password(SERVICE, name) is not None else "missing"
        print(f"{name:24} {state:8} {info['description']}")


def cmd_set(args, registry):
    require_known(args.name, registry)
    if args.from_file:
        value = Path(args.from_file).read_text(encoding="utf-8").strip()
    else:
        value = getpass.getpass(f"Value for {args.name} (input hidden): ")
        if value != getpass.getpass("Again to confirm: "):
            sys.exit("Values didn't match; nothing saved.")
    if not value:
        sys.exit("Empty value; nothing saved.")
    keyring.set_password(SERVICE, args.name, value)
    print(f"Saved {args.name}.")
    if args.from_file:
        print(f"You can now delete {args.from_file}.")


def cmd_remove(args, registry):
    try:
        keyring.delete_password(SERVICE, args.name)
    except keyring.errors.PasswordDeleteError:
        sys.exit(f"{args.name} is not set.")
    (OUT_DIR / args.name).unlink(missing_ok=True)
    print(f"Removed {args.name}.")


def write_private(path, value):
    """Write a file readable only by its owner (on macOS and Linux)."""
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(value)


def cmd_export(args, registry):
    OUT_DIR.mkdir(mode=0o700, exist_ok=True)
    exported, missing = [], []
    for name in registry:
        value = keyring.get_password(SERVICE, name)
        if value is None:
            # Don't leave a stale copy of a secret that has been removed.
            (OUT_DIR / name).unlink(missing_ok=True)
            missing.append(name)
        else:
            write_private(OUT_DIR / name, value)
            exported.append(name)
    print(f"Exported {len(exported)} secret(s) to secrets/.")
    if missing:
        print("Not set: " + ", ".join(missing))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Manage Home Agent secrets.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show which secrets are set (never values)")
    p_set = sub.add_parser("set", help="store a secret (prompts for the value)")
    p_set.add_argument("name")
    p_set.add_argument("--from-file", help="read the value from a file instead")
    p_remove = sub.add_parser("remove", help="delete a secret from the store")
    p_remove.add_argument("name")
    sub.add_parser("export", help="write secrets to secrets/ for Docker")
    args = parser.parse_args(argv)

    commands = {
        "status": cmd_status,
        "set": cmd_set,
        "remove": cmd_remove,
        "export": cmd_export,
    }
    commands[args.command](args, load_registry())


if __name__ == "__main__":
    main()
