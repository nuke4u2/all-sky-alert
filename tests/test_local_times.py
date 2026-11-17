from copy import deepcopy
from dataclasses import replace
from datetime import date

import pytest
from fastapi.testclient import TestClient

from all_sky_alert.cli import main, test_alert as build_test_alert
from all_sky_alert.domain import parse
from all_sky_alert.notifications import render_message
from all_sky_alert.presentation import local_time, local_text
from all_sky_alert.service import Service
from all_sky_alert.web import create_app


@pytest.mark.parametrize("stamp, expected", [
    ("2026-11-17T02:30:00Z", "Mon Nov 16 2026, 09:30 PM EST"),
    ("2026-07-17T02:30:00Z", "Thu Jul 16 2026, 10:30 PM EDT"),
    ("2026-03-08T06:59:00Z", "Sun Mar 08 2026, 01:59 AM EST"),
    ("2026-03-08T07:00:00Z", "Sun Mar 08 2026, 03:00 AM EDT"),
    ("2026-11-01T05:30:00Z", "Sun Nov 01 2026, 01:30 AM EDT"),
    ("2026-11-01T06:30:00Z", "Sun Nov 01 2026, 01:30 AM EST"),
])
def test_immediate_mail_localizes_all_timestamps_without_changing_payload(stamp, expected):
    alert = build_test_alert()
    event = alert['prediction']['candidate']
    event.update(start=stamp, peak=stamp, end=stamp)
    alert['prediction']['assessment']['conditions']['metrics'] = {
        'seeing_index': {'value': 4, 'valid_at': stamp, 'issued_at': stamp, 'retrieved_at': stamp}}
    original = deepcopy(alert)
    body = render_message(alert, 'fixture@example.org', 'America/New_York').get_content()
    for label in ('Start', 'Peak', 'End', 'Valid', 'model run', 'Seeing retrieved'):
        assert f'{label}: {expected}' in body
    assert 'UTC' not in body and stamp not in body
    assert alert == original


def test_display_precision_unknown_and_persisted_health_text():
    assert local_time('2026-11-17T02:30:04.123456Z', 'America/New_York', precise=True) == '2026-11-16 21:30:04.123456 EST'
    assert local_time(None, 'America/New_York') == 'Unknown'
    body = local_text('RDPS run 2026-11-17T02:30:00+00:00; through 2026-11-17T03:30:00Z; <script>', 'America/New_York')
    assert '09:30 PM EST' in body and '10:30 PM EST' in body
    assert '2026-11-17T' not in body
    # Ordinary text and malformed source timestamps must not break rendering.
    assert local_text('Unknown; 2026-99-17T02:30:00Z', 'America/New_York') == 'Unknown; 2026-99-17T02:30:00Z'


def test_test_email_preview_uses_observer_timezone(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'config.toml'
    path.write_text('[observer]\nlatitude=0\nlongitude=0\ntimezone="America/New_York"\n')
    monkeypatch.setattr('all_sky_alert.cli.utcnow', lambda: parse('2026-11-17T02:30:00Z'))
    monkeypatch.setattr('sys.argv', ['all-sky-alert', '--config', str(path), 'test-email'])
    main()
    body = capsys.readouterr().out
    assert 'Mon Nov 16 2026, 09:30 PM EST' in body and 'UTC' not in body
    assert not (tmp_path / 'data').exists()


def test_service_mail_and_ui_use_configured_timezone(config, store):
    config = replace(config, observer=replace(config.observer, timezone='America/New_York'))
    config.values['gmail']['enabled'] = True
    now = parse('2026-11-17T02:30:00Z')
    service = Service(config, store=store, detectors=[], sources={}, clock=lambda: now)
    try:
        assert service.adapters['gmail'].timezone == 'America/New_York'
        store.health('worker:slow', 'ok', now, 'Forecast retrieved 2026-11-17T02:30:00+00:00 <script>')
        with TestClient(create_app(service, background=False), base_url='http://localhost') as client:
            home = client.get('/').text
            assert '<body data-timezone="America/New_York">' in home
            assert 'Mon Nov 16 2026, 09:30 PM EST' in home
            assert 'Forecast retrieved Mon Nov 16 2026, 09:30 PM EST &lt;script&gt;' in home
            assert '2026-11-17T02:30:00' not in home
            # Transport/storage remain UTC; only presentation is converted.
            assert client.get('/api/health').json()['overall']['evaluated_at'] == '2026-11-17T02:30:00+00:00'
    finally:
        service.close()


@pytest.mark.parametrize('mode', ['daily_digest', 'weekly_digest'])
def test_digest_renders_timezone_for_each_instant_across_dst(mode):
    payload = {'id': 'fixture', 'kind': 'digest', 'mode': mode, 'day': date(2026, 11, 1).isoformat(),
               'timezone': 'America/New_York', 'window_start': '2026-11-01T05:30:00Z',
               'window_end': '2026-11-01T06:30:00Z', 'generated_at': '2026-11-01T05:00:00Z',
               'items': [], 'omitted': 0, 'notes': [], 'base_url': ''}
    body = render_message(payload, 'fixture@example.org').get_content()
    assert '01:30 AM EDT' in body and '01:30 AM EST' in body
    assert 'UTC' not in body
