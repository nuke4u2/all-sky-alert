"""Deployment sequencing uses fake Git, pip and systemd; no live updates/mail."""
from contextlib import nullcontext
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from urllib.error import URLError

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("update_pi", ROOT/"scripts/update_pi.py")
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


@pytest.fixture
def project(tmp_path):
    package = tmp_path/"src/all_sky_alert"
    package.mkdir(parents=True)
    (package/"__init__.py").write_text("# fixture\n")
    (package/"registry.py").write_text("# current detector registrations\n")
    (tmp_path/"README.md").write_text("fixture")
    (tmp_path/"constraints.txt").write_text("# fixture constraints\n")
    (tmp_path/"pyproject.toml").write_text('[project]\ndependencies=["numpy>=1.26,<3"]\n[project.optional-dependencies]\ngmail=["google-auth>=2.29,<3"]\n')
    (tmp_path/"custom.toml").write_text("# private test config, never part of a build\n")
    return tmp_path


def fake_commands(monkeypatch, project, failed=None):
    commands = []
    python = project/".venv/bin/python"
    config = project/"custom.toml"
    def run(command, *, cwd, capture=False, input=None):
        command = [str(p) for p in command]
        commands.append(command)
        stdout = ""
        step = None
        if command[:2] == ["git", "pull"]:
            step = "pull"
        elif "wheel" in command:
            step = "build"
            wheels = Path(command[command.index("--wheel-dir")+1])
            wheels.mkdir()
            (wheels/"all_sky_alert-0.1.0-py3-none-any.whl").write_bytes(b"fixture wheel")
        elif "pip" in command and "install" in command:
            step = "install" if "--force-reinstall" in command else "dependencies"
        elif "-c" in command and command[command.index("-c")+1] == updater.VERIFY_INSTALL:
            step = "verify"
            assert set(json.loads(input)) == {"__init__.py", "registry.py"}
        elif command[-1] == "check":
            step = "check"
        elif str(project/"scripts/install_systemd.py") in command:
            step = "unit"
            assert command[-1] == "--no-start"
        if command[:2] == ["systemctl", "show"]:
            if "--property=WorkingDirectory" in command:
                stdout = str(project)
            elif "--property=ExecStart" in command:
                stdout = f"{{ path={python} ; argv[]={python} -m all_sky_alert --config {config} serve ; }}"
        elif "-c" in command and "json.dumps({k:s[k]" in command[command.index("-c")+1]:
            stdout = json.dumps({"bind_host": "127.0.0.1", "port": 8008, "allowed_hosts": ["localhost"]})
        if failed == step and step is not None:
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(stdout=stdout)
    def ready(settings):
        commands.append(["HTTP ready"])
        if failed == "readiness":
            raise RuntimeError("readiness failed")
    monkeypatch.setattr(updater, "run", run)
    monkeypatch.setattr(updater, "wait_ready", ready)
    return commands


def test_update_stops_pulls_installs_verifies_checks_unit_and_starts(monkeypatch, project, tmp_path):
    commands = fake_commands(monkeypatch, project)
    updater.update(project, project/"custom.toml", extras=("gmail",), scratch_dir=tmp_path)
    assert commands[0] == ["sudo", "systemctl", "stop", updater.UNIT_NAME]
    assert commands[1] == ["git", "pull", "--ff-only"]
    installs = [c for c in commands if "pip" in c and "install" in c]
    assert len(installs) == 2
    assert "numpy>=1.26,<3" in installs[0] and "google-auth>=2.29,<3" in installs[0]
    assert "--force-reinstall" not in installs[0]
    assert "--no-deps" in installs[1] and "--force-reinstall" in installs[1]
    assert installs[1][-1].endswith(".whl")
    assert all(c[0] == str(project/".venv/bin/python") for c in installs)
    verified = next(i for i, c in enumerate(commands) if updater.VERIFY_INSTALL in c)
    checked = next(i for i, c in enumerate(commands) if c[-1] == "check")
    unit = next(i for i, c in enumerate(commands) if str(project/"scripts/install_systemd.py") in c)
    started = commands.index(["sudo", "systemctl", "start", updater.UNIT_NAME])
    assert verified < checked < unit < started < commands.index(["HTTP ready"])
    assert (project/"custom.toml").read_text().startswith("# private")
    assert not any("venv" in c or "activate" in c or "config-sync" in c for c in commands)


@pytest.mark.parametrize("failed", ["pull", "build", "dependencies", "install", "verify", "check", "unit", "readiness"])
def test_failure_does_not_continue_or_leave_failed_start_running(monkeypatch, project, tmp_path, failed):
    commands = fake_commands(monkeypatch, project, failed)
    with pytest.raises((subprocess.CalledProcessError, RuntimeError)):
        updater.update(project, project/"custom.toml", scratch_dir=tmp_path)
    if failed == "readiness":
        assert commands[-1] == ["sudo", "systemctl", "stop", updater.UNIT_NAME]
    else:
        assert ["sudo", "systemctl", "start", updater.UNIT_NAME] not in commands


def test_already_pulled_update_skips_git_pull(monkeypatch, project, tmp_path):
    commands = fake_commands(monkeypatch, project)
    updater.update(project, project/"custom.toml", pull=False, scratch_dir=tmp_path)
    assert not any(c[:2] == ["git", "pull"] for c in commands)


def test_clean_build_does_not_copy_private_runtime_files(project, tmp_path):
    (project/"data").mkdir()
    (project/"data/private.sqlite3").write_bytes(b"private fixture")
    (project/"src/all_sky_alert/__pycache__").mkdir()
    (project/"src/all_sky_alert/__pycache__/registry.pyc").write_bytes(b"old fixture")
    staged = tmp_path/"build"
    updater.stage_source(project, staged)
    assert (staged/"src/all_sky_alert/registry.py").read_bytes() == (project/"src/all_sky_alert/registry.py").read_bytes()
    assert not (staged/"custom.toml").exists() and not (staged/"data").exists()
    assert not (staged/"src/all_sky_alert/__pycache__").exists()
    with pytest.raises(ValueError, match="Unknown extras"):
        updater.dependency_requirements(staged, ("undeclared",))


@pytest.mark.parametrize("mismatch", [False, True, "outside"])
def test_byte_verification_detects_stale_or_wrong_imported_copy(tmp_path, monkeypatch, capsys, mismatch):
    prefix = tmp_path/"venv"
    package = (tmp_path/"other" if mismatch == "outside" else prefix)/"lib/all_sky_alert"
    package.mkdir(parents=True)
    (package/"__init__.py").write_bytes(b"current")
    expected = {"__init__.py": hashlib.sha256(b"current").hexdigest()}
    if mismatch is True:
        (package/"__init__.py").write_bytes(b"stale same-version package")
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setitem(sys.modules, "all_sky_alert", SimpleNamespace(__file__=str(package/"__init__.py")))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(expected)))
    if mismatch:
        with pytest.raises(SystemExit, match="differs|outside"):
            exec(updater.VERIFY_INSTALL, {})
    else:
        exec(updater.VERIFY_INSTALL, {})
        assert "matches checkout" in capsys.readouterr().out


def test_effective_unit_mismatch_is_rejected(monkeypatch, project):
    monkeypatch.setattr(updater, "run", lambda *a, **k: SimpleNamespace(stdout="/another/checkout"))
    with pytest.raises(ValueError, match="another checkout"):
        updater.verify_unit(project, project/".venv/bin/python", project/"custom.toml")


@pytest.mark.parametrize("bind,expected", [("0.0.0.0", "127.0.0.1"), ("::", "[::1]"), ("192.168.1.20", "192.168.1.20")])
def test_readiness_uses_explicit_bind_and_allowed_host_without_proxy(monkeypatch, bind, expected):
    def open(request, timeout):
        assert request.full_url == f"http://{expected}:8008/api/health"
        assert request.get_header("Host") == "allsky-pi4.local:8008"
        return nullcontext(SimpleNamespace(status=200))
    def opener(handler):
        assert handler.proxies == {}
        return SimpleNamespace(open=open)
    monkeypatch.setattr(updater, "build_opener", opener)
    updater.wait_ready({"bind_host": bind, "port": 8008, "allowed_hosts": ["allsky-pi4.local"]})


def test_readiness_timeout_is_a_failure(monkeypatch):
    def fail(*args, **kwargs):
        raise URLError("not listening")
    monkeypatch.setattr(updater, "build_opener", lambda *a: SimpleNamespace(open=fail))
    values = iter((0., 0., 31.))
    monkeypatch.setattr(updater.time, "monotonic", lambda: next(values))
    monkeypatch.setattr(updater.time, "sleep", lambda *a: None)
    with pytest.raises(RuntimeError, match="startup deadline"):
        updater.wait_ready({"bind_host": "127.0.0.1", "port": 8008, "allowed_hosts": ["localhost"]})


def test_main_preflight_failure_runs_no_update(monkeypatch, project, capsys):
    monkeypatch.setattr(updater, "PROJECT_DIR", project)
    monkeypatch.setattr(updater, "preflight", lambda *a: (_ for _ in ()).throw(ValueError("missing existing venv")))
    monkeypatch.setattr(updater, "update", lambda *a, **k: pytest.fail("Failed preflight started an update"))
    assert updater.main([]) == 1
    assert "missing existing venv" in capsys.readouterr().err
