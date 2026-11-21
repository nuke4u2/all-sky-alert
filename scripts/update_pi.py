"""Update the installed Pi service from its checkout using the existing venv.

Run as the normal service account: python3 scripts/update_pi.py
Only systemd/unit operations use sudo. Failures after stopping leave it stopped.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import tomllib
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener


PROJECT_DIR = Path(__file__).resolve().parents[1]
UNIT_NAME = "all-sky-alert.service"


def run(command, *, cwd, capture=False, input=None):
    command = [str(part) for part in command]
    print("+ " + shlex.join(command), flush=True)
    environment = dict(os.environ)
    # Shell activation and Python path overrides must not select another copy.
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    return subprocess.run(command, cwd=cwd, env=environment, text=True,
                          capture_output=capture, input=input, check=True)


def preflight(project, config):
    if sys.platform != "linux":
        raise ValueError("Run this updater on the Linux/systemd Pi")
    if os.geteuid() == 0:
        raise ValueError("Run as the normal service account without sudo; only service operations use sudo")
    for command in ("git", "sudo", "systemctl", "systemd-analyze", "install"):
        if shutil.which(command) is None:
            raise ValueError(f"Required command is missing: {command}")
    if not Path("/run/systemd/system").is_dir():
        raise ValueError("systemd is not running")
    python = project/".venv/bin/python"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("Existing Pi .venv/bin/python is missing; follow the one-time README setup")
    settings = (project/".venv/pyvenv.cfg").read_text(encoding="utf-8")
    if not re.search(r"^include-system-site-packages\s*=\s*true\s*$", settings, re.M | re.I):
        raise ValueError("The Pi venv must have include-system-site-packages = true for apt camera dependencies; it will not be recreated automatically")
    if not config.is_file():
        raise ValueError(f"Configuration file does not exist: {config}")
    root = run(["git", "rev-parse", "--show-toplevel"], cwd=project, capture=True).stdout.strip()
    if Path(root).resolve() != project:
        raise ValueError("Updater is not at the expected checkout root")
    modified = run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=project, capture=True).stdout
    if modified.strip():
        raise ValueError("Tracked files have local changes; preserve/resolve them before updating. No reset or stash was performed")
    # Confirm a service exists before stopping/pulling/installing anything.
    state = run(["systemctl", "show", UNIT_NAME, "--property=LoadState", "--value"], cwd=project, capture=True).stdout.strip()
    if state != "loaded":
        raise ValueError("Install the systemd service first with scripts/install_systemd.py")


def stage_source(project, destination):
    # A fresh build tree avoids stale build/lib outputs in the live checkout.
    # Never copy private config, data, secrets or credentials into a build.
    destination.mkdir()
    for name in ("pyproject.toml", "README.md", "constraints.txt"):
        shutil.copy2(project/name, destination/name)
    shutil.copytree(project/"src/all_sky_alert", destination/"src/all_sky_alert",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))


def dependency_requirements(source, extras):
    project = tomllib.loads((source/"pyproject.toml").read_text(encoding="utf-8"))["project"]
    optional = project.get("optional-dependencies", {})
    if set(extras)-set(optional):
        raise ValueError("Unknown extras: " + ", ".join(sorted(set(extras)-set(optional))))
    # pyproject.toml remains the only owner of dependency declarations.
    return list(dict.fromkeys([*project["dependencies"], *[requirement for name in extras for requirement in optional[name]]]))


VERIFY_INSTALL = r'''
import hashlib, json, pathlib, sys
import all_sky_alert
expected = json.loads(sys.stdin.read())
package = pathlib.Path(all_sky_alert.__file__).resolve().parent
if not package.is_relative_to(pathlib.Path(sys.prefix).resolve()):
    raise SystemExit("Application imports outside the service venv: " + str(package))
for name, digest in expected.items():
    path = package / name
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise SystemExit("Installed package differs from checkout: " + name)
print("Installed package matches checkout: " + str(package) + " (" + str(len(expected)) + " files)")
'''


def verify_installation(python, source, *, cwd):
    package = source/"src/all_sky_alert"
    expected = {p.relative_to(package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(package.rglob("*")) if p.is_file() and "__pycache__" not in p.parts
                and p.suffix not in (".pyc", ".pyo")}
    if not expected:
        raise ValueError("Build contains no application files")
    run([python, "-c", VERIFY_INSTALL], cwd=cwd, input=json.dumps(expected))


def verify_unit(project, python, config):
    directory = run(["systemctl", "show", UNIT_NAME, "--property=WorkingDirectory", "--value"], cwd=project, capture=True).stdout.strip()
    start = run(["systemctl", "show", UNIT_NAME, "--property=ExecStart", "--value"], cwd=project, capture=True).stdout
    match = re.search(r"\bpath=(.*?)\s*;\s*argv\[\]=", start)
    config_match = re.search(r"--config\s+(.*?)\s+serve(?:\s|;|$)", start)
    if (directory != str(project) or not match or match[1].strip() != str(python)
            or not config_match or config_match[1].strip('"') != str(config)):
        raise ValueError("Effective systemd unit uses another checkout/interpreter/config; inspect systemctl cat all-sky-alert for overrides")


def wait_ready(settings, timeout=30):
    bind = {"0.0.0.0": "127.0.0.1", "::": "::1", "localhost": "127.0.0.1"}.get(settings["bind_host"], settings["bind_host"])
    host = settings["allowed_hosts"][0]
    address = f"[{bind}]" if ":" in bind else bind
    host = f"[{host}]" if ":" in host else host
    request = Request(f"http://{address}:{settings['port']}/api/health", headers={"Host": f"{host}:{settings['port']}"})
    opener = build_opener(ProxyHandler({}))  # Never send local readiness checks through a shell proxy.
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(request, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            pass
        time.sleep(.5)
    raise RuntimeError("Service did not serve /api/health before the startup deadline")


def update(project, config, *, pull=True, extras=(), scratch_dir=None):
    python = project/".venv/bin/python"
    run(["sudo", "systemctl", "stop", UNIT_NAME], cwd=project)
    # Pull is bounded to fast-forward; never discard local changes or rewrite history.
    if pull:
        run(["git", "pull", "--ff-only"], cwd=project)
    run(["git", "log", "-1", "--oneline"], cwd=project)
    with TemporaryDirectory(prefix="all-sky-update-", dir=scratch_dir) as temporary:
        directory = Path(temporary)
        source, wheels = directory/"source", directory/"wheels"
        stage_source(project, source)
        requirements = dependency_requirements(source, extras)
        run([python, "-m", "pip", "wheel", "--no-deps", "--no-cache-dir", "--wheel-dir", wheels, source], cwd=directory)
        built = list(wheels.glob("all_sky_alert-*.whl"))
        if len(built) != 1:
            raise ValueError("Expected exactly one freshly built application wheel")
        if requirements:
            run([python, "-m", "pip", "install", "--no-cache-dir", "-c", source/"constraints.txt", *requirements], cwd=directory)
        # Force-reinstall only our own wheel. Do not force-reinstall apt NumPy or
        # the camera stack; their compatible installed versions remain usable.
        run([python, "-m", "pip", "install", "--no-deps", "--no-cache-dir", "--force-reinstall", built[0]], cwd=directory)
        verify_installation(python, source, cwd=directory)
        run([python, "-m", "all_sky_alert", "runtime-info"], cwd=directory)
        run([python, "-m", "all_sky_alert", "--config", config, "check"], cwd=directory)
        # Read only networking values from the validated config, never OAuth or
        # other private values, to check actual HTTP startup after systemd start.
        network = run([python, "-c", "import json,sys; from all_sky_alert.config import load; "
                       "s=load(sys.argv[1]).values['service']; "
                       "print(json.dumps({k:s[k] for k in ('bind_host','port','allowed_hosts')}))", config],
                      cwd=directory, capture=True)
        settings = json.loads(network.stdout)
    # Run the freshly pulled installer, rather than a stale imported helper.
    run([python, project/"scripts/install_systemd.py", "--config", config, "--no-start"], cwd=project)
    verify_unit(project, python, config)
    run(["sudo", "systemctl", "reset-failed", UNIT_NAME], cwd=project)
    run(["sudo", "systemctl", "start", UNIT_NAME], cwd=project)
    try:
        run(["systemctl", "is-active", "--quiet", UNIT_NAME], cwd=project)
        wait_ready(settings)
        run(["systemctl", "is-active", "--quiet", UNIT_NAME], cwd=project)
        run(["systemctl", "status", UNIT_NAME, "--no-pager", "--full"], cwd=project)
    except (RuntimeError, OSError, subprocess.CalledProcessError):
        run(["sudo", "systemctl", "stop", UNIT_NAME], cwd=project)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.toml"), help="Existing config path relative to checkout")
    parser.add_argument("--no-pull", action="store_true", help="Install the already-pulled checkout")
    parser.add_argument("--extras", default="", help="Optional dependency extras to refresh, e.g. gmail,hrrr")
    parser.add_argument("--scratch-dir", type=Path, help="Existing scratch directory for disposable builds")
    args = parser.parse_args(argv)
    project = PROJECT_DIR.resolve()
    config = (project/args.config).resolve()
    try:
        preflight(project, config)
        update(project, config, pull=not args.no_pull,
               extras=tuple(filter(None, (name.strip() for name in args.extras.split(",")))),
               scratch_dir=args.scratch_dir)
        print("Installed application verified and service started. Check the dashboard and source health.")
        return 0
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"Update failed: {error}", file=sys.stderr)
        print("No automatic rollback or restart is attempted after a failed update. Fix the error and rerun.", file=sys.stderr)
        print("Logs: journalctl -u all-sky-alert -n 100 --no-pager -o cat", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
