"""Installer checks use temporary files and mocked commands; never touch systemd."""

import importlib.util
from pathlib import Path, PurePosixPath
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("install_systemd", ROOT / "scripts/install_systemd.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def test_render_preserves_service_contract_and_literal_paths():
    project = PurePosixPath('/home/pi/my sky%name/$checkout')
    config = project / 'observer "one".toml'
    template = (ROOT / "deploy/systemd/all-sky-alert.service").read_text(encoding="utf-8")
    unit = installer.render_unit(template, "pi", project, config)
    assert 'User=pi\n' in unit
    assert 'WorkingDirectory=/home/pi/my sky%%name/$checkout\n' in unit
    assert 'ExecStart=:"/home/pi/my sky%%name/$checkout/.venv/bin/python"' in unit
    assert '--config "/home/pi/my sky%%name/$checkout/observer \\"one\\".toml" serve' in unit
    for contract in ("Wants=network-online.target", "SupplementaryGroups=video",
                     "Restart=on-failure", "TimeoutStopSec=300", "UMask=0077",
                     "WantedBy=multi-user.target", "StandardOutput=journal"):
        assert contract in unit
    assert "@SERVICE_USER@" not in unit


@pytest.mark.parametrize("user", ["root", "pi\nUser=root", "0"])
def test_rejects_unsafe_service_account(user):
    with pytest.raises(ValueError):
        installer.render_unit("User=@SERVICE_USER@", user, PurePosixPath("/srv/sky"), PurePosixPath("/srv/config.toml"))


def test_rejects_unit_line_injection():
    with pytest.raises(ValueError, match="control characters"):
        installer.unit_quote("/srv/config\nExecStart=/bin/false")


@pytest.mark.parametrize("no_start", [False, True])
def test_install_validates_then_enables_boot_and_optionally_restarts(monkeypatch, no_start):
    commands = []

    def record(command):
        commands.append(command)
        if command[:2] == ["systemd-analyze", "verify"]:
            assert command[2].read_bytes() == b"[Unit]\nDescription=test\n"

    monkeypatch.setattr(installer, "run", record)
    installer.install_unit("[Unit]\nDescription=test\n", no_start=no_start)
    assert commands[0][:2] == ["systemd-analyze", "verify"]
    assert commands[1][:8] == ["sudo", "install", "-o", "root", "-g", "root", "-m", "644"]
    assert commands[1][-1] == Path("/etc/systemd/system/all-sky-alert.service")
    expected = [
        ["sudo", "systemctl", "daemon-reload"],
        ["sudo", "systemctl", "enable", installer.UNIT_NAME],
    ]
    if not no_start:
        expected.append(["sudo", "systemctl", "restart", installer.UNIT_NAME])
    expected.append(["systemctl", "is-enabled", installer.UNIT_NAME])
    if not no_start:
        expected.append(["systemctl", "is-active", installer.UNIT_NAME])
    assert commands[2:] == expected


@pytest.mark.parametrize("failed_step", ["verify", "install", "daemon-reload", "enable", "restart"])
def test_command_failure_stops_subsequent_actions(monkeypatch, failed_step):
    commands = []

    def fail(command):
        commands.append(command)
        if failed_step in command:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(installer, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        installer.install_unit("[Unit]\nDescription=test\n")
    assert failed_step in commands[-1]
    if failed_step == "verify":
        assert len(commands) == 1


def test_dry_run_uses_checkout_and_never_installs_or_runs_commands(monkeypatch, tmp_path, capsys):
    template_dir = tmp_path / "deploy/systemd"
    template_dir.mkdir(parents=True)
    (template_dir / installer.UNIT_NAME).write_text("User=@SERVICE_USER@\nWorkingDirectory=@PROJECT_DIR@\n")
    monkeypatch.setattr(installer, "PROJECT_DIR", tmp_path)

    def preflight(project, config):
        assert project == tmp_path
        assert config == (tmp_path / "custom.toml").resolve()
        return "pi"

    def forbidden(*args, **kwargs):
        pytest.fail("Dry run attempted a system change or command")

    monkeypatch.setattr(installer, "preflight", preflight)
    monkeypatch.setattr(installer, "install_unit", forbidden)
    monkeypatch.setattr(installer, "run", forbidden)
    assert installer.main(["--dry-run", "--config", "custom.toml"]) == 0
    output = capsys.readouterr().out
    assert "User=pi" in output
    assert "Dry run:" in output


def test_preflight_failure_returns_nonzero_without_installing(monkeypatch, capsys):
    def fail(*args):
        raise ValueError("Missing Pi venv")

    monkeypatch.setattr(installer, "preflight", fail)
    monkeypatch.setattr(installer, "install_unit", lambda *args, **kwargs: pytest.fail("Unexpected install"))
    assert installer.main([]) == 1
    assert "Missing Pi venv" in capsys.readouterr().err
