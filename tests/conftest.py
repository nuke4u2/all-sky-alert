from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from all_sky_alert.config import Config, DEFAULTS
from all_sky_alert.domain import Candidate, Conditions, Metric, Observer, Source, iso
from all_sky_alert.providers import FIELDS
from all_sky_alert.storage import Store


@pytest.fixture(autouse=True)
def block_live_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Live network forbidden in normal tests")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    # Google auth uses requests; ensure accidental refresh cannot reach the network either.
    try:
        import requests
        monkeypatch.setattr(requests.sessions.Session, "request", blocked)
    except ImportError:
        pass


@pytest.fixture
def now():
    return datetime(2026, 10, 6, 18, tzinfo=UTC)


@pytest.fixture
def config(tmp_path):
    values = deepcopy(DEFAULTS)
    values["service"]["data_dir"] = "data"
    values["service"]["prediction_days"] = 2
    return Config(Observer(51.4779, 0, 46, "Europe/London"), values, tmp_path)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "predictions.sqlite3")


@pytest.fixture
def event(config, now):
    return Candidate("stable-event", "planetary", "planetary.window", config.observer.key,
        "2026-10-06", "Jupiter detail", now + timedelta(hours=2), now + timedelta(hours=3),
        now + timedelta(hours=4), 70, 0.98, "planetary", ["Large disk"],
        [Source("fixture", "https://example.org/", iso(now), "fixture")],
        {"altitude_deg": 65, "azimuth_deg": 180}, {"body": "Jupiter", "version": 1})


@pytest.fixture
def conditions(now):
    values = {"cloud_total": 5, "cloud_low": 5, "cloud_mid": 50, "cloud_high": 40,
              "humidity": 45, "visibility": 40000, "wind_surface": 1, "wind_250hpa": 10}
    return Conditions({name: Metric(value, "m" if name == "visibility" else "m/s" if "wind" in name else "%",
        0.8, "fixture", "fixture", iso(now)) for name, value in values.items()}, "fresh", iso(now),
        Source("fixture", "https://example.org/", iso(now), "forecast"))


@pytest.fixture
def weather(now):
    start = now.replace(hour=0)
    times = [int((start + timedelta(hours=i)).timestamp()) for i in range(24 * 5)]
    values = {"cloud_cover": 5, "cloud_cover_low": 5, "cloud_cover_mid": 50, "cloud_cover_high": 40,
              "relative_humidity_2m": 45, "visibility": 40000, "wind_speed_10m": 1, "wind_speed_250hPa": 10,
              "temperature_2m":5,"dew_point_2m":0}
    return {"hourly": {"time": times, **{key: [value] * len(times) for key, value in values.items()}},
            "hourly_units": {key: unit for key, (_, unit, _, _) in FIELDS.items()}}


class FakeProvider:
    def __init__(self, data):
        self.data, self.calls, self.error = data, 0, None

    def fetch(self, observer, days):
        self.calls += 1
        if self.error:
            raise self.error
        return deepcopy(self.data)


class FakeConditions:
    def __init__(self, conditions):
        self.conditions, self.refreshes, self.requests = conditions, 0, []

    def refresh(self, now):
        self.refreshes += 1

    def at(self, when, geometry):
        self.requests.append(when)
        return self.conditions


class RecordingAdapter:
    name = "recording"

    def __init__(self):
        self.alerts, self.error = [], None

    def send(self, alert):
        if self.error:
            raise self.error
        self.alerts.append(alert)
        return "fake-message-id"
