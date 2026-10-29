"""Install and enable All Sky Alert on a systemd Linux host (Raspberry Pi).

Run as the normal service account, with the Pi venv already installed. Only the
unit installation/systemctl commands use sudo. No application config is edited.
"""

import argparse
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory


PROJECT_DIR = Path(__file__).resolve().parents[1]
UNIT_NAME = "all-sky-alert.service"
UNIT_PATH = Path("/etc/systemd/system") / UNIT_NAME


def unit_quote(value):
    """Quote an ExecStart argument, preserving literal paths and %-specifiers."""
    value = str(value)
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Paths must not contain control characters")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def working_directory_value(project):
    # WorkingDirectory consumes a literal path, unlike ExecStart's word parser.
    # Quotes would become part of the path. Reject line continuation/stripping.
    value = str(project)
    if (any(ord(char) < 32 or ord(char) == 127 for char in value)
            or value != value.strip() or value.endswith("\\")):
        raise ValueError("Project path contains unsupported whitespace or a trailing backslash")
    return value.replace("%", "%%")


def render_unit(template, user, project, config):
    if user == "root" or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.-]*\$?", user):
        raise ValueError("The service requires a normal, non-root Linux account")
    replacements = {
        "SERVICE_USER": user,
        "PROJECT_DIR": working_directory_value(project),
        "PYTHON": unit_quote(project / ".venv/bin/python"),
        "CONFIG": unit_quote(config),
    }
    return re.sub(r"@(SERVICE_USER|PROJECT_DIR|PYTHON|CONFIG)@",
                  lambda match: replacements[match[1]], template)


def preflight(project, config):
    if sys.platform != "linux":
        raise ValueError("Run this installer locally on the Pi, under Linux/systemd")
    import grp
    import pwd

    if os.geteuid() == 0:
        raise ValueError("Run as the normal service account without sudo; the installer uses sudo when needed")
    user = pwd.getpwuid(os.geteuid()).pw_name
    for command in ("sudo", "install", "systemctl", "systemd-analyze"):
        if shutil.which(command) is None:
            raise ValueError(f"Required command is missing: {command}")
    if not Path("/run/systemd/system").is_dir():
        raise ValueError("systemd is not running on this host")
    try:
        grp.getgrnam("video")
    except KeyError as error:
        raise ValueError("The video group is missing; install the Pi OS camera prerequisites") from error
    python = project / ".venv/bin/python"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError(f"Create the Pi Linux venv and install requirements first: {python}")
    if not config.is_file() or not os.access(config, os.R_OK):
        raise ValueError(f"Create a readable, configured TOML file first: {config}")
    # Settings replaces the config atomically in its parent directory.
    if not os.access(config.parent, os.W_OK | os.X_OK):
        raise ValueError(f"The service account needs write/search access to the config directory: {config.parent}")
    return user


def run(command):
    print("+ " + shlex.join([str(part) for part in command]), flush=True)
    subprocess.run([str(part) for part in command], check=True)


def install_unit(unit, *, no_start=False):
    with TemporaryDirectory(prefix="all-sky-systemd-") as temporary:
        rendered = Path(temporary) / UNIT_NAME
        rendered.write_text(unit, encoding="utf-8", newline="\n")
        # Validate before changing the installed unit or boot enablement.
        run(["systemd-analyze", "verify", rendered])
        run(["sudo", "install", "-o", "root", "-g", "root", "-m", "644", rendered, UNIT_PATH])
        run(["sudo", "systemctl", "daemon-reload"])
        run(["sudo", "systemctl", "enable", UNIT_NAME])
        if not no_start:
            # restart also starts an inactive unit and applies a replacement unit.
            run(["sudo", "systemctl", "restart", UNIT_NAME])
        run(["systemctl", "is-enabled", UNIT_NAME])
        if not no_start:
            run(["systemctl", "is-active", UNIT_NAME])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.toml"),
                        help="Config path, relative to the checkout unless absolute (default: config.toml)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Check prerequisites and print the unit; no sudo, writes, or systemctl actions")
    parser.add_argument("--no-start", action="store_true",
                        help="Install and enable at boot without starting/restarting now")
    args = parser.parse_args(argv)
    try:
        config = (PROJECT_DIR / args.config).resolve()
        user = preflight(PROJECT_DIR, config)
        template = (PROJECT_DIR / "deploy/systemd" / UNIT_NAME).read_text(encoding="utf-8")
        unit = render_unit(template, user, PROJECT_DIR, config)
        print(unit, flush=True)
        if args.dry_run:
            print(f"Dry run: would install {UNIT_PATH}, reload systemd and enable boot startup.")
            if not args.no_start:
                print("Would also start/restart All Sky Alert now.")
            return 0
        print("Stop any foreground All Sky Alert server before installation.", flush=True)
        install_unit(unit, no_start=args.no_start)
        print("All Sky Alert is enabled for boot startup.")
        print("Inspect status: systemctl status all-sky-alert --no-pager")
        print("Inspect logs: journalctl -u all-sky-alert -f")
        if args.no_start:
            print("Start when ready: sudo systemctl start all-sky-alert")
        else:
            print("Check the dashboard/health endpoint for application readiness.")
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Installation failed: {error}", file=sys.stderr)
        print("Inspect: systemctl status all-sky-alert --no-pager", file=sys.stderr)
        print("Logs: journalctl -u all-sky-alert -n 100 --no-pager", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
