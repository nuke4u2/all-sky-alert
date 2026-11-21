"""New detector tables stay valid across CLI, web settings and installed smoke."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tomllib

from fastapi.testclient import TestClient
import pytest
import tomlkit

from all_sky_alert import cli, config as configuration, registry
from all_sky_alert.assessment import PROFILES
from all_sky_alert.config import DEFAULTS, from_dict, load
from all_sky_alert.config_edit import ConfigEditor, field_definitions
from all_sky_alert.mail_policy import validate_types


ROOT = Path(__file__).resolve().parents[1]
NEW_DETECTORS = {"meteors", "lunar_occultations"}


def private_config(tmp_path):
    values = deepcopy(DEFAULTS)
    for settings in values["detectors"].values():
        settings["enabled"] = False
    for name in NEW_DETECTORS:
        values["detectors"][name]["enabled"] = True
    values["detectors"]["lunar_occultations"]["horizon_days"] = 2
    for settings in values["providers"].values():
        settings["enabled"] = False
    values["service"]["data_dir"] = "private-data"
    values["notifications"]["enabled"] = values["gmail"]["enabled"] = False
    values["atmosphere"]["enabled"] = values["atmosphere"]["canadian"]["enabled"] = False
    values["camera"]["enabled"] = False
    values["map"]["tiles_enabled"] = False
    path = tmp_path/"private.toml"
    path.write_text(tomlkit.dumps({"observer": {"latitude": 0, "longitude": 0, "timezone": "UTC"}, **values}), encoding="utf-8")
    return path


def test_registry_schema_example_profiles_settings_and_mail_agree():
    example = tomllib.loads((ROOT/"config.example.toml").read_text(encoding="utf-8"))
    assert NEW_DETECTORS <= set(registry.DETECTORS)
    assert set(example["detectors"]) == set(DEFAULTS["detectors"]) == set(registry.DETECTORS)
    fields = field_definitions()
    for name, spec in registry.DETECTORS.items():
        assert DEFAULTS["detectors"][name] == spec.defaults
        assert spec.profiles and all(profile in PROFILES for profile in spec.profiles)
        assert set(example["detectors"][name]) <= set(spec.defaults)
        assert f"detectors.{name}.enabled" in fields
        validate_types([name])
    loaded = load(ROOT/"config.example.toml")
    assert all(loaded.values["detectors"][name]["enabled"] for name in NEW_DETECTORS)


def test_runtime_info_needs_no_config_database_or_service(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Runtime diagnostics attempted to read config or construct a service")
    monkeypatch.setattr(cli, "load", forbidden)
    monkeypatch.setattr(cli, "Service", forbidden)
    monkeypatch.setattr(sys, "argv", ["all-sky-alert", "--config", str(tmp_path/"missing.toml"), "runtime-info"])
    cli.main()
    info = json.loads(capsys.readouterr().out)
    assert info["python"] == sys.executable
    assert Path(info["config_module"]) == Path(configuration.__file__).resolve()
    assert Path(info["registry_module"]) == Path(registry.__file__).resolve()
    assert info["registered_detectors"] == info["configuration_detectors"]
    assert NEW_DETECTORS <= set(info["registered_detectors"])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("command", ["check", "serve"])
def test_check_and_serve_boot_with_both_new_tables(tmp_path, monkeypatch, capsys, command):
    path = private_config(tmp_path)
    monkeypatch.setattr(sys, "argv", ["all-sky-alert", "--config", str(path), command])
    if command == "serve":
        from all_sky_alert import server, web
        original_create_app = web.create_app
        def create_app(service):
            assert set(service.detector_names) == NEW_DETECTORS
            return original_create_app(service, background=False)
        def run(server):
            # Real CLI/service/web composition, without opening ports, workers,
            # camera hardware or live providers in normal offline tests.
            with TestClient(server.config.app, base_url="http://localhost") as client:
                for route in ("/", "/api/health", "/settings"):
                    response = client.get(route)
                    assert response.status_code == 200
                settings = client.get("/settings").text
                assert "Meteor showers (three peak nights)" in settings
                assert "Moon occulting planets" in settings
        monkeypatch.setattr(web, "create_app", create_app)
        monkeypatch.setattr(server.ApplicationServer, "run", run)
    cli.main()
    output = capsys.readouterr().out
    runtime = json.loads(output.splitlines()[0].removeprefix("Runtime: "))
    assert NEW_DETECTORS <= set(runtime["registered_detectors"])
    assert NEW_DETECTORS <= set(runtime["configuration_detectors"])
    if command == "check":
        assert "Configuration valid; SQLite writable" in output
        assert "Initialized detectors: lunar_occultations, meteors" in output
    assert (tmp_path/"private-data"/"all-sky-alert.sqlite3").is_file()


def test_settings_sync_retains_enabled_modules_and_adds_safe_legacy_defaults(tmp_path):
    path = private_config(tmp_path)
    ConfigEditor(path).sync()
    loaded = load(path)
    assert all(loaded.values["detectors"][name]["enabled"] for name in NEW_DETECTORS)
    legacy = tmp_path/"legacy.toml"
    legacy.write_text('[observer]\nlatitude=0\nlongitude=0\ntimezone="UTC"\n', encoding="utf-8")
    ConfigEditor(legacy).sync()
    old = load(legacy)
    assert all(not old.values["detectors"][name]["enabled"] for name in NEW_DETECTORS)


@pytest.mark.parametrize("command", ["check", "serve"])
def test_unknown_detector_remains_error_with_runtime_identity(tmp_path, monkeypatch, capsys, command):
    path = tmp_path/"typo.toml"
    path.write_text('[observer]\nlatitude=0\nlongitude=0\ntimezone="UTC"\n[detectors.meteor]\nenabled=true\n')
    monkeypatch.setattr(sys, "argv", ["all-sky-alert", "--config", str(path), command])
    monkeypatch.setattr(cli, "Service", lambda *a, **k: pytest.fail("Invalid configuration initialized service"))
    with pytest.raises(SystemExit) as failure:
        cli.main()
    assert failure.value.code == 1
    captured = capsys.readouterr()
    assert "Unknown configuration: detectors.meteor." in captured.err
    assert "Registered detector families" in captured.err and "runtime-info" in captured.err
    assert "Runtime: " in captured.out
    assert not (tmp_path/"data").exists()


def test_new_modules_still_reject_invalid_settings(tmp_path):
    observer = {"latitude": 0, "longitude": 0, "timezone": "UTC"}
    for name in NEW_DETECTORS:
        with pytest.raises(ValueError, match="Unknown configuration"):
            from_dict({"observer": observer, "detectors": {name: {"unknown_option": True}}}, tmp_path)


def test_installed_smoke_rejects_registry_drift(monkeypatch):
    spec = importlib.util.spec_from_file_location("pi_native_smoke", ROOT/"scripts/pi_native_smoke.py")
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    example = tomllib.loads((ROOT/"config.example.toml").read_text(encoding="utf-8"))
    info = smoke.verify_detector_schema(example)
    monkeypatch.setattr(smoke, "runtime_info", lambda: {**info, "registered_detectors": [n for n in info["registered_detectors"] if n != "meteors"]})
    with pytest.raises(ValueError, match="does not match this checkout"):
        smoke.verify_detector_schema(example)
