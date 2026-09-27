"""Start Home Agent automatically when you sign in to Windows, by putting a
shortcut to scripts/startup.py in your Startup folder (no admin rights, no
console window). On the Mac this will be a launchd agent instead.

  python scripts/autostart.py install
  python scripts/autostart.py remove
  python scripts/autostart.py status
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHORTCUT = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/Home Agent.lnk"


def pythonw():
    """The venv's windowless Python."""
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return candidate if candidate.exists() else Path(sys.executable)


def install():
    target, script = pythonw(), ROOT / "scripts" / "startup.py"
    command = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:SHORTCUT); "
        "$s.TargetPath = $env:TARGET; $s.Arguments = '\"' + $env:SCRIPT + '\"'; "
        "$s.WorkingDirectory = $env:ROOT; $s.Description = 'Start Home Agent'; $s.Save()"
    )
    env = {**os.environ, "SHORTCUT": str(SHORTCUT), "TARGET": str(target), "SCRIPT": str(script), "ROOT": str(ROOT)}
    subprocess.run(["powershell", "-NoProfile", "-Command", command], check=True, env=env)
    print(f"Installed: {SHORTCUT}\nRuns {script.name} with {target.name} at sign-in. Log: logs/startup.log")


def remove():
    if SHORTCUT.exists():
        SHORTCUT.unlink()
        print(f"Removed: {SHORTCUT}")
    else:
        print("Not installed.")


def status():
    print(f"{'Installed' if SHORTCUT.exists() else 'Not installed'}: {SHORTCUT}")


def main():
    if sys.platform != "win32":
        sys.exit("This installer is for Windows. (The Mac will use a launchd agent.)")
    commands = {"install": install, "remove": remove, "status": status}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    commands[sys.argv[1]]()


if __name__ == "__main__":
    main()
