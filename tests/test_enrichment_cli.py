"""Operator catalog commands with private storage and synthetic upstream data."""
import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from all_sky_alert import cli
from all_sky_alert.domain import iso
from all_sky_alert.locking import ServiceLock
from all_sky_alert.standing_data import URL
from test_standing_data import archive_bytes


@pytest.fixture
def catalog_command(tmp_path, monkeypatch, now):
    path = tmp_path/"private.toml"
    path.write_text('[observer]\nlatitude=0\nlongitude=0\ntimezone="UTC"\n'
                    '[service]\ndata_dir="private-state"\n'
                    '[providers.enrichment]\nenabled=true\nmode="vrs"\n'
                    '[gmail]\nenabled=true\ndestination="fixture@example.org"\n', encoding="utf-8")
    calls = []
    reply = {"response": httpx.Response(200, content=archive_bytes(), headers={"ETag": '"fixture"'})}
    def upstream(request):
        assert str(request.url) == URL  # No ADS-B/weather/credentials/mail endpoints.
        calls.append(request)
        return reply["response"]
    original_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs:
        original_client(transport=httpx.MockTransport(upstream), **kwargs))
    monkeypatch.setattr(cli, "Service", lambda *args, **kwargs: pytest.fail("Catalog command initialized Service"))
    monkeypatch.setattr(cli, "utcnow", lambda: now)
    def run(*flags):
        monkeypatch.setattr("sys.argv", ["all-sky-alert", "--config", str(path), "enrichment-update", *flags])
        cli.main()
    return path, calls, reply, run


def test_load_cache_skip_fresh_and_force_conditional_validation(catalog_command, capsys, monkeypatch, now):
    path, calls, reply, run = catalog_command
    before = path.read_bytes()
    run()
    loaded = json.loads(capsys.readouterr().out)
    database = path.parent/"private-state"/"aircraft-standing.sqlite3"
    assert loaded["status"] == "updated" and Path(loaded["cache"]) == database
    assert loaded["database_bytes"] == database.stat().st_size > 0
    assert loaded["counts"]["routes"] == 3 and len(calls) == 1
    cached_bytes = database.read_bytes()
    run()
    assert json.loads(capsys.readouterr().out)["status"] == "cached" and len(calls) == 1
    reply["response"] = httpx.Response(304)
    monkeypatch.setattr(cli, "utcnow", lambda: now+timedelta(minutes=5))
    run("--force")
    checked = json.loads(capsys.readouterr().out)
    assert checked["status"] == "not_modified" and len(calls) == 2
    assert calls[-1].headers["If-None-Match"] == '"fixture"'
    assert checked["sha256"] == loaded["sha256"] and checked["downloaded_at"] == loaded["downloaded_at"]
    assert checked["verified_at"] == iso(now+timedelta(minutes=5)) != loaded["verified_at"]
    assert database.read_bytes() == cached_bytes and path.read_bytes() == before
    assert not (path.parent/"secrets").exists()


@pytest.mark.parametrize("failure", ["503", "badzip"])
def test_failed_force_keeps_previous_cache_reports_failure_and_honors_backoff(catalog_command, capsys, failure):
    path, calls, reply, run = catalog_command
    run()
    loaded = json.loads(capsys.readouterr().out)
    database = Path(loaded["cache"])
    before = database.read_bytes()
    reply["response"] = (httpx.Response(503, headers={"Retry-After": "7200"}) if failure == "503"
                         else httpx.Response(200, content=b"invalid zip"))
    with pytest.raises(SystemExit) as stopped:
        run("--force")
    assert stopped.value.code == 1
    output = capsys.readouterr()
    failed = json.loads(output.out)
    assert failed["status"] == "error" and failed["error"] and failed["next_attempt"]
    assert "did not complete" in output.err and database.read_bytes() == before
    assert len(calls) == 2
    with pytest.raises(SystemExit) as stopped:
        run("--force")
    assert stopped.value.code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "backoff"
    assert len(calls) == 2 and database.read_bytes() == before


def test_running_service_lock_blocks_before_storage_or_fetch(catalog_command, capsys):
    path, calls, _, run = catalog_command
    directory = path.parent/"private-state"
    with ServiceLock(directory), pytest.raises(SystemExit) as stopped:
        run("--force")
    assert stopped.value.code == 1 and "stop it first" in capsys.readouterr().err
    assert not calls and not (directory/"all-sky-alert.sqlite3").exists()


@pytest.mark.parametrize("enabled,mode", [(False, "local"), (False, "vrs"), (True, "local")])
def test_disabled_or_local_mode_needs_explicit_settings_before_fetch(catalog_command, capsys, enabled, mode):
    path, calls, _, run = catalog_command
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace('enabled=true\nmode="vrs"', f'enabled={str(enabled).lower()}\nmode="{mode}"'), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(SystemExit) as stopped:
        run("--force")
    assert stopped.value.code == 1 and "select Public catalog" in capsys.readouterr().err
    assert not calls and not (path.parent/"private-state").exists()
    assert path.read_bytes() == before
