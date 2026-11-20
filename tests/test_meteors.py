"""Offline shower geometry, local-night identity, weather and delivery contracts."""
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from all_sky_alert.astronomy import Astronomy
from all_sky_alert.assessment import PROFILES, meteors
from all_sky_alert.config import from_dict
from all_sky_alert.config_edit import field_definitions
from all_sky_alert.domain import Conditions, DetectionContext, Metric, Observer, iso, parse
from all_sky_alert.mail_policy import immediate_allowed, validate_types
from all_sky_alert.meteors import MeteorShowerDetector, SHOWERS
from all_sky_alert.policy import qualified
from all_sky_alert.registry import DETECTORS
from all_sky_alert.service import Service
from all_sky_alert.web import create_app
from conftest import FakeConditions, RecordingAdapter


class FixtureSky:
    version = "fixture"

    def solar_longitude_peak(self, year, longitude):
        return datetime(year, 1, 1, 3, tzinfo=UTC)

    def position(self, body, when, observer):
        return {"altitude_deg": (-18 if when.hour >= 18 or when.hour < 6 else 20)
                if body == "Sun" else -10}

    def radiant_position(self, ra, dec, when, observer):
        return {"altitude_deg": 75, "azimuth_deg": 180}

    def moon_illumination(self, when):
        return 0.5


def fixture_batch(monkeypatch, now=None, days=4, sky=None, observer=None):
    monkeypatch.setattr("all_sky_alert.meteors.SHOWERS", (SHOWERS[0],))
    return MeteorShowerDetector(deepcopy(MeteorShowerDetector.defaults)).detect(DetectionContext(
        observer or Observer(40, -74, 0, "UTC"),
        now or datetime(2026, 12, 30, 12, tzinfo=UTC), days, sky or FixtureSky()))


@pytest.mark.parametrize("year,longitude,expected", [
    # Independent IMO calendars, Quadrantid descriptions; 2026 and 2027 differ
    # by six hours, so fixed annual calendar dates or equinox-of-date fail.
    (2026, 283.15, "2026-01-03T21:15:00Z"),
    (2027, 283.15, "2027-01-04T03:25:00Z"),
])
def test_nominal_peak_against_imo_reference(year, longitude, expected):
    actual = Astronomy().solar_longitude_peak(year, longitude)
    assert abs((actual-parse(expected)).total_seconds()) < 20*60


def test_three_local_nights_across_new_year_and_stable_identity(monkeypatch):
    batch = fixture_batch(monkeypatch)
    assert {e.scope for e in batch.candidates} == {"2026-12-30", "2026-12-31", "2027-01-01"}
    assert {e.metadata["peak_night_offset"] for e in batch.candidates} == {-1, 0, 1}
    assert len({e.id for e in batch.candidates}) == 3
    assert all(e.scope in batch.scopes and e.start <= e.peak <= e.end for e in batch.candidates)
    later = fixture_batch(monkeypatch, datetime(2026, 12, 31, 0, tzinfo=UTC))
    assert {e.id for e in batch.candidates} == {e.id for e in later.candidates}
    other = fixture_batch(monkeypatch, observer=Observer(-40, 74, 0, "UTC"))
    assert {e.id for e in other.candidates}.isdisjoint(e.id for e in batch.candidates)


def test_local_scope_and_dst_noon_windows(monkeypatch):
    class AutumnSky(FixtureSky):
        def solar_longitude_peak(self, year, longitude):
            return datetime(year, 11, 1, 6, tzinfo=UTC)
        def position(self, body, when, observer):
            return {"altitude_deg": -18}
    site = Observer(40, -74, 0, "America/New_York")
    batch = fixture_batch(monkeypatch, datetime(2026, 10, 30, tzinfo=UTC), 4, AutumnSky(), site)
    assert len(batch.candidates) == 3
    central = next(e for e in batch.candidates if e.metadata["peak_night_offset"] == 0)
    assert central.scope == "2026-10-31"
    assert central.end-central.start == timedelta(hours=25)
    assert central.start.astimezone(ZoneInfo(site.timezone)).hour == 12


def test_daylight_below_horizon_and_disjoint_windows(monkeypatch):
    class Daylight(FixtureSky):
        def position(self, *args):
            return {"altitude_deg": 10}
    assert fixture_batch(monkeypatch, sky=Daylight()).candidates == []
    class LowRadiant(FixtureSky):
        def radiant_position(self, *args):
            return {"altitude_deg": -1, "azimuth_deg": 0}
    assert fixture_batch(monkeypatch, sky=LowRadiant()).candidates == []
    class SplitSky(FixtureSky):
        def radiant_position(self, ra, dec, when, observer):
            return {"altitude_deg": 75 if when.hour < 2 else -5, "azimuth_deg": 180}
    batch = fixture_batch(monkeypatch, sky=SplitSky())
    assert len(batch.candidates) == 3
    assert all(e.start.hour == 0 and e.end.hour == 1 and e.end.minute == 45 for e in batch.candidates)


def test_real_geminid_geometry_has_three_dark_visible_nights():
    observer = Observer(51.4779, 0, 46, "Europe/London")
    astronomy = Astronomy()
    batch = MeteorShowerDetector(MeteorShowerDetector.defaults).detect(DetectionContext(
        observer, datetime(2026, 12, 11, 12, tzinfo=UTC), 6, astronomy))
    events = [e for e in batch.candidates if e.metadata["shower_code"] == "GEM"]
    assert len(events) == 3
    assert {e.metadata["peak_night_offset"] for e in events} == {-1, 0, 1}
    for event in events:
        for when in (event.start, event.peak, event.end):
            assert astronomy.position("Sun", when, observer)["altitude_deg"] <= -12
            assert astronomy.radiant_position(112, 33, when, observer)["altitude_deg"] >= 15
        assert 0 <= event.geometry["moon_illumination"] <= 1
        assert event.valid_until is None  # Local ephemeris has no observation TTL.
        assert event.sources and "J2000" in event.sources[-1].method


def test_clouds_moonlight_unknown_and_consumed_freshness(monkeypatch, config, conditions, now):
    event = fixture_batch(monkeypatch).candidates[0]
    clear = meteors(event, conditions)
    assert qualified(event, clear, config.policy(event.kind), now)
    cloudy = replace(conditions, metrics={"cloud_total": replace(conditions.metrics["cloud_total"], value=100)})
    assert meteors(event, cloudy).quality == 0
    assert not qualified(event, meteors(event, cloudy), config.policy(event.kind), now)
    assert event.interest == 85  # Neither clouds nor Moon rewrite intrinsic interest.
    bright = replace(event, geometry={**event.geometry, "moon_altitude_deg": 40, "moon_illumination": 1})
    assert meteors(bright, conditions).quality < clear.quality
    unknown = meteors(event, Conditions({}, "outside_forecast", None, None))
    assert unknown.quality is None and not unknown.usable_for_alerts
    stale = meteors(event, replace(conditions, status="stale"))
    assert not qualified(event, stale, config.policy(event.kind), now)
    # Supplementary seeing and its expiry are unrelated to meteor visibility.
    extra = Metric(1, "category", 0.2, "fixture", "fixture", iso(now), "stale",
                   valid_until=iso(now-timedelta(hours=1)))
    unrelated = replace(conditions, metrics={**conditions.metrics, "seeing_index": extra})
    assert meteors(event, unrelated) == replace(clear, conditions=unrelated)
    expiry = iso(now+timedelta(minutes=30))
    cloud = replace(conditions.metrics["cloud_total"], valid_until=expiry)
    assessed = meteors(event, replace(conditions, metrics={"cloud_total": cloud}))
    assert assessed.valid_until == expiry
    assert not qualified(event, assessed, config.policy(event.kind), now+timedelta(hours=1))
    assert not meteors(event, replace(conditions, metrics={"cloud_total": replace(cloud, status="stale")})).usable_for_alerts
    assert PROFILES["meteors"].required_metrics == ("cloud_total",)


def test_registry_settings_and_validation(tmp_path):
    raw = {"observer": {"latitude": 40, "longitude": -74, "timezone": "America/New_York"}}
    config = from_dict(raw, tmp_path)
    assert not config.values["detectors"]["meteors"]["enabled"]
    assert "detectors.meteors.enabled" in field_definitions()
    assert DETECTORS["meteors"].profiles == ("meteors",)
    validate_types(["meteors"])
    for key, value in (("minimum_altitude_deg", -1), ("maximum_sun_altitude_deg", 0)):
        supplied = {**raw, "detectors": {"meteors": {key: value}}}
        with pytest.raises(ValueError):
            from_dict(supplied, tmp_path)


def test_shared_service_alerts_weather_updates_dedup_and_rendering(monkeypatch, config, store, conditions):
    now = datetime(2026, 12, 30, 12, tzinfo=UTC)
    monkeypatch.setattr("all_sky_alert.meteors.SHOWERS", (SHOWERS[0],))
    config = replace(config, observer=Observer(40, -74, 0, "UTC"))
    config.values["detectors"]["meteors"]["enabled"] = True
    config.values["service"]["prediction_days"] = 4
    adapter = RecordingAdapter()
    shared = FakeConditions(conditions)
    service = Service(config, store=store, conditions=shared, astronomy=FixtureSky(),
                      detectors=[MeteorShowerDetector(config.values["detectors"]["meteors"])],
                      adapters=[adapter], clock=lambda: now)
    try:
        service.tick(now, force=False)
        predictions = service.upcoming()
        assert len(predictions) == 3 and len(adapter.alerts) == 1
        event_id = predictions[0]["candidate"]["id"]
        candidate = next(e for e in fixture_batch(monkeypatch).candidates if e.id == event_id)
        assert immediate_allowed({**config.values["gmail"], "enabled": True}, candidate)
        service.tick(now+timedelta(minutes=5), force=False)
        assert len(adapter.alerts) == 1
        shared.conditions = replace(conditions, metrics={"cloud_total": replace(conditions.metrics["cloud_total"], value=100)})
        service.tick(now+timedelta(minutes=10), force=False)
        detail = store.detail(event_id, config.observer.key)
        assert detail["assessment"]["quality"] == 0
        assert len(detail["alerts"]) == 1 and len(adapter.alerts) == 1
        assert len(shared.requests) == 9  # Reassess every cycle despite cached geometry.
        with TestClient(create_app(service, background=False), base_url="http://localhost") as client:
            assert "Quadrantids" in client.get("/").text
            page = client.get(f"/events/{event_id}")
            assert page.status_code == 200
            assert "International Meteor Organization" in page.text
            assert "Cloud forecast" in page.text
        health = next(h for h in store.health_rows() if h["name"] == "detector:meteors")
        assert health["status"] == "ok"
    finally:
        service.close()
