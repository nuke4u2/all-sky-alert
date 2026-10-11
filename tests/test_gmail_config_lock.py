import base64
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from all_sky_alert.cli import main, test_alert as build_test_alert
from all_sky_alert.config import load
from all_sky_alert.locking import ServiceLock
from all_sky_alert.notifications import GmailAdapter, SafeDeliveryError, UncertainDeliveryError, private_write


def test_gmail_adapter_with_fake_oauth_and_transport(tmp_path, monkeypatch):
    credentials = pytest.importorskip("google.oauth2.credentials")
    class FakeCredentials:
        valid, token = True, "fake-oauth-token"
    monkeypatch.setattr(credentials.Credentials, "from_authorized_user_file", lambda *a: FakeCredentials())
    path = tmp_path / "token.json"
    path.write_text("{}")
    def handler(request):
        import json
        assert request.headers["Authorization"] == "Bearer fake-oauth-token"
        raw = json.loads(request.content)["raw"]
        message = base64.urlsafe_b64decode(raw).decode()
        assert "recipient@example.org" in message
        assert "not a sky prediction" in message
        return httpx.Response(200, json={"id": "accepted-id"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = GmailAdapter(path, "recipient@example.org", client)
        assert adapter.send(build_test_alert()) == "accepted-id"
    for status, error in [(401, SafeDeliveryError), (429, SafeDeliveryError), (503, UncertainDeliveryError)]:
        with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status))) as client:
            with pytest.raises(error):
                GmailAdapter(path, "recipient@example.org", client).send(build_test_alert())
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={}))) as client:
        with pytest.raises(UncertainDeliveryError):
            GmailAdapter(path, "recipient@example.org", client).send(build_test_alert())


def test_missing_credentials_safe_failure(tmp_path):
    with httpx.Client() as client:
        with pytest.raises(SafeDeliveryError):
            GmailAdapter(tmp_path / "absent.json", "recipient@example.org", client).send(build_test_alert())


def test_preview_does_not_send_or_read_credentials(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.toml"
    config.write_text('[observer]\nlatitude=0\nlongitude=0\ntimezone="UTC"\n')
    monkeypatch.setattr("sys.argv", ["all-sky-alert", "--config", str(config), "test-email"])
    main()
    assert "Preview only" in capsys.readouterr().out
    assert not (tmp_path / "secrets").exists()


@pytest.mark.parametrize("extra", ['[service]\nprediction_days=0\n', '[observerx]\nx=1\n',
    '[detectors.planetary]\nbodies=["Pluto"]\n', '[notifications]\nminimum_quality=101\n',
    '[service]\nallowed_hosts=["*"]\n', '[notifications.overrides."eclipse.total"]\ninvalid=1\n',
    '[notifications.overrides."eclipse.total"]\nenabled="false"\n', '[service]\nallowed_hosts="localhost"\n'])
def test_config_rejects_mistakes(tmp_path, extra):
    config = tmp_path / "config.toml"
    config.write_text('[observer]\nlatitude=0\nlongitude=0\ntimezone="UTC"\n' + extra)
    with pytest.raises(ValueError):
        load(config)


def test_config_requires_location_and_allows_policy_override(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[service]\nrecent_hours=3\n')
    with pytest.raises(ValueError):
        load(path)
    path.write_text('[observer]\nlatitude=0\nlongitude=0\ntimezone="UTC"\n[notifications.overrides."eclipse.total"]\nminimum_quality=0\n')
    config = load(path)
    assert config.policy("eclipse.total")["minimum_quality"] == 0
    assert config.policy("planetary.window")["minimum_quality"] == 55


def test_service_lock_rejects_second_coordinator_and_releases(tmp_path):
    with ServiceLock(tmp_path):
        with pytest.raises(RuntimeError):
            with ServiceLock(tmp_path):
                pass
    with ServiceLock(tmp_path):
        pass


def test_private_token_write_is_atomic(tmp_path):
    path = tmp_path / "secrets" / "token.json"
    private_write(path, "first")
    private_write(path, "second")
    assert path.read_text() == "second"
    assert not path.with_name("token.json.tmp").exists()
