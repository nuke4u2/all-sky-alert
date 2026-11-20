"""Offline local finite-disk contacts, weather/seeing, identity and delivery."""
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from math import sqrt
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from all_sky_alert.assessment import planetary
from all_sky_alert.canadian import CanadianSeeing
from all_sky_alert.config import from_dict, load
from all_sky_alert.config_edit import field_definitions
from all_sky_alert.digests import build
from all_sky_alert.domain import Conditions, DetectionContext, Metric, Observer, iso, parse
from all_sky_alert.mail_policy import immediate_allowed
from all_sky_alert.notifications import render_message
from all_sky_alert.occultations import LunarOccultationDetector, LunarOccultationEphemeris
from all_sky_alert.policy import alert_stages, qualified
from all_sky_alert.providers import ConditionsService
from all_sky_alert.registry import DETECTORS
from all_sky_alert.service import Service
from all_sky_alert.web import create_app
from conftest import FakeConditions, FakeProvider, RecordingAdapter
from test_canadian import SeeingProvider


REFERENCE = json.loads((Path(__file__).parent/"fixtures/lunar-occultation-iota-2026.json").read_text())


@pytest.mark.parametrize("station", REFERENCE["stations"], ids=lambda s: s["name"])
def test_contacts_against_independent_iota_reference(station):
    observer = Observer(station["latitude"], station["longitude"], 0, "America/New_York")
    now = parse(REFERENCE["date"]+"T00:00:00Z")
    settings = {**LunarOccultationDetector.defaults, "bodies": ["Jupiter"], "horizon_days": 1,
                "minimum_altitude_deg": 0}  # Reference includes Chicago's low first contact.
    detector = LunarOccultationDetector(settings)
    events = detector.detect(DetectionContext(observer, now, 1, None)).candidates
    assert len(events) == 2
    for event in events:
        expected = parse(REFERENCE["date"]+"T"+station[event.metadata["transition"]]+"Z")
        assert abs((event.peak-expected).total_seconds()) < REFERENCE["tolerance_seconds"]
        contacts = event.metadata["contacts"]
        assert [c["phase"] for c in contacts] == ["first_contact", "second_contact", "third_contact", "fourth_contact"]
        assert all(c["observable"] for c in contacts)
        assert event.valid_until is None
        assert event.metadata["ephemeris"]["kernel"] == "JPL DE421"
        assert event.metadata["ephemeris"]["kernel_sha256"]
        assert "Numerical tolerance is not physical timing uncertainty" in event.metadata["contact_definition"]
        assert event.scope in detector.detect(DetectionContext(observer, now, 1, None)).scopes
        # Finite solid-disk edges, not just a point center crossing the lunar limb.
        for contact in contacts:
            separation, moon, planet = detector.ephemeris.disk("Jupiter", parse(contact["time"]), observer)
            expected_radius = moon+planet if contact["phase"] in ("first_contact", "fourth_contact") else moon-planet
            assert abs(separation-expected_radius) < .00002


class SyntheticDisk(LunarOccultationEphemeris):
    """A smooth tangential track with no overlapping three-hour coarse sample."""
    def __init__(self, center, impact):
        self.center, self.impact = center, impact
    def disk(self, body, when, observer):
        seconds = (when-self.center).total_seconds()
        return sqrt((seconds/7200)**2+self.impact**2), .25, .01
    def scan(self, body, times, observer):
        values = np.array([self.disk(body, t, observer) for t in times])
        return values[:, 0], values[:, 1], values[:, 2]


@pytest.mark.parametrize("impact,partial,expected_count", [(0.01, False, 1), (.25, True, 1), (.2601, True, 0)])
def test_minimum_search_finite_disk_partial_and_near_miss(impact, partial, expected_count):
    now = parse("2026-10-06T00:00:00Z")
    ephemeris = SyntheticDisk(now+timedelta(hours=4, seconds=47), impact)
    events = list(ephemeris.occultations("Mars", now, now+timedelta(days=1), Observer(40, -74, 0, "UTC")))
    assert len(events) == expected_count
    if events:
        assert events[0]["partial"] == partial
        assert len(events[0]["contacts"]) == (2 if partial else 4)


def test_short_graze_between_coarse_samples_is_not_lost():
    now = parse("2026-10-06T00:00:00Z")
    # External radius .26°, speed 1/7200 degree per second; two-second overlap.
    ephemeris = SyntheticDisk(now+timedelta(hours=4, seconds=47), sqrt(.26**2-(1/7200)**2))
    events = list(ephemeris.occultations("Mars", now, now+timedelta(days=1), Observer(40, -74, 0, "UTC")))
    event, = events
    assert event["partial"]
    assert 1.8 < (event["contacts"][-1]["time"]-event["contacts"][0]["time"]).total_seconds() < 2.2


class FixtureEphemeris:
    version = "fixture"
    provenance = {"library": "fixture", "version": "fixture", "coverage": "fixture"}

    def __init__(self, now, partial=False):
        self.contacts = {"first_contact": now+timedelta(hours=2), "second_contact": now+timedelta(hours=2, minutes=1),
                         "third_contact": now+timedelta(hours=3), "fourth_contact": now+timedelta(hours=3, minutes=1)}
        self.maximum = now+timedelta(hours=2, minutes=30)
        self.partial, self.calls = partial, 0
        self.geometry = lambda when: {"altitude_deg": 75., "geometric_altitude_deg": 75., "moon_altitude_deg": 75.,
                                      "sun_altitude_deg": -15., "solar_elongation_deg": 80., "azimuth_deg": 180.}

    def at(self, body, when, observer):
        return self.geometry(when)

    def occultations(self, body, start, end, observer):
        self.calls += 1
        if start <= self.maximum < end:
            yield {"body": body, "maximum": self.maximum, "partial": self.partial, "minimum_separation_deg": .25 if self.partial else .01,
                   "contacts": [{"phase": p, "time": t} for p, t in self.contacts.items()
                                if not self.partial or p in ("first_contact", "fourth_contact")]}


def fixture_detection(config, now, ephemeris=None, **settings):
    detector = LunarOccultationDetector({**deepcopy(LunarOccultationDetector.defaults), "bodies": ["Saturn"],
                                        "horizon_days": 1, **settings}, ephemeris or FixtureEphemeris(now))
    return detector, detector.detect(DetectionContext(config.observer, now, 1, None))


def test_identity_scope_complete_contacts_and_visibility_clipping(config, now):
    ephemeris = FixtureEphemeris(now)
    detector, full = fixture_detection(config, now, ephemeris)
    assert len(full.candidates) == 2 and len(full.scopes) == 3
    assert len({e.id for e in full.candidates}) == 2
    repeated = detector.detect(DetectionContext(config.observer, now+timedelta(minutes=17), 1, None))
    assert {e.id for e in full.candidates} == {e.id for e in repeated.candidates}
    original = ephemeris.geometry
    cutoff = ephemeris.contacts["first_contact"]+timedelta(seconds=30)
    ephemeris.geometry = lambda t: {**original(t), "moon_altitude_deg": 10+(t-cutoff).total_seconds()/60}
    _, clipped = fixture_detection(config, now, ephemeris)
    assert {e.id for e in clipped.candidates} == {e.id for e in full.candidates}
    disappearance = next(e for e in clipped.candidates if e.metadata["transition"] == "disappearance")
    assert abs((disappearance.start-cutoff).total_seconds()) < .2
    assert disappearance.peak >= disappearance.start
    assert not disappearance.metadata["contacts"][0]["observable"]
    other = detector.detect(DetectionContext(replace(config.observer, longitude=12), now, 1, None))
    assert {e.id for e in other.candidates}.isdisjoint(e.id for e in full.candidates)


def test_hidden_interval_and_visible_preparation_do_not_authorize_contacts(config, now):
    ephemeris = FixtureEphemeris(now)
    original = ephemeris.geometry
    # Visible only before disappearance, and while the planet is entirely hidden.
    def geometry(t):
        visible = t < ephemeris.contacts["first_contact"]-timedelta(seconds=1) or (
            ephemeris.contacts["second_contact"]+timedelta(seconds=1) < t < ephemeris.contacts["third_contact"]-timedelta(seconds=1))
        return {**original(t), "geometric_altitude_deg": 75 if visible else -1}
    ephemeris.geometry = geometry
    _, batch = fixture_detection(config, now, ephemeris)
    assert batch.candidates == [] and batch.scopes
    ephemeris.geometry = lambda t: {**original(t), "geometric_altitude_deg": 75 if t > ephemeris.maximum else -1}
    _, batch = fixture_detection(config, now, ephemeris)
    event, = batch.candidates
    assert event.metadata["transition"] == "reappearance"


@pytest.mark.parametrize("field,value", [("sun_altitude_deg", 10), ("solar_elongation_deg", 5),
                                        ("moon_altitude_deg", -1), ("geometric_altitude_deg", -1)])
def test_daylight_elongation_and_horizon_exclusions(config, now, field, value):
    ephemeris = FixtureEphemeris(now)
    original = ephemeris.geometry
    ephemeris.geometry = lambda t: {**original(t), field: value}
    assert fixture_detection(config, now, ephemeris)[1].candidates == []


def test_partial_overlap_does_not_claim_disappearance(config, now):
    event, = fixture_detection(config, now, FixtureEphemeris(now, partial=True))[1].candidates
    assert event.kind == "lunar_occultation.partial"
    assert len(event.metadata["contacts"]) == 2
    assert event.metadata["contacts"][0]["label"] == "Partial overlap begins"
    assert "Nominal partial overlap" in event.reasons[-1]


def test_high_interest_keeps_cloud_weather_and_seeing_screening(config, conditions, now):
    event = fixture_detection(config, now)[1].candidates[0]
    policy = config.policy(event.kind)
    clear = planetary(event, conditions)
    assert event.interest == 89 < policy["exceptional_interest"]
    assert qualified(event, clear, policy, now)
    assert immediate_allowed({**config.values["gmail"], "enabled": True}, event)
    cloudy = replace(conditions, metrics={**conditions.metrics, "cloud_total": replace(conditions.metrics["cloud_total"], value=100)})
    assert planetary(event, cloudy).quality == 0
    for bad in (cloudy, replace(conditions, status="stale"), Conditions({}, "outside_forecast", None, None)):
        assert alert_stages(event, planetary(event, bad), now, policy) == []
    assert event.interest == 89
    for index in (0, 1, 5):
        metric = Metric(index, "category", .8, "fixture", "native ordinal", iso(event.peak),
                        issued_at=iso(now), retrieved_at=iso(now), valid_until=iso(now+timedelta(minutes=30)))
        with_seeing = replace(conditions, metrics={**conditions.metrics, "seeing_index": metric})
        assessment = planetary(event, with_seeing)
        assert qualified(event, assessment, policy, now) == (index == 5)
        assert assessment.quality is None if index == 0 else assessment.quality is not None
        assert not qualified(event, assessment, policy, now+timedelta(hours=1))
        stale = replace(with_seeing, metrics={**with_seeing.metrics, "seeing_index": replace(metric, status="stale")})
        assert not qualified(event, planetary(event, stale), policy, now)


def test_service_shared_weather_seeing_cadence_alerts_and_details(config, store, weather, now):
    for settings in config.values["detectors"].values():
        settings["enabled"] = False
    config.values["detectors"]["lunar_occultations"].update(enabled=True, bodies=["Saturn"], horizon_days=1)
    forecast = FakeProvider(weather)
    seeing_provider = SeeingProvider(now, value=5)
    seeing = CanadianSeeing(store, seeing_provider, config.observer,
                           {**config.values["atmosphere"]["canadian"], "enabled": True})
    conditions = ConditionsService(store, forecast, config.observer, config.values["atmosphere"], seeing=seeing)
    adapter = RecordingAdapter()
    service = Service(config, store=store, conditions=conditions, adapters=[adapter], sources={}, clock=lambda: now)
    try:
        detector, = service.detectors
        detector.ephemeris = FixtureEphemeris(now)
        assert conditions.consumers == seeing.consumers == ["lunar_occultations"]
        service.tick(now, force=False)
        predictions = service.upcoming()
        assert len(predictions) == len(adapter.alerts) == 2
        assert forecast.calls == 1 and len(seeing_provider.point_calls) == 2
        assert predictions[0]["assessment"]["conditions"]["metrics"]["seeing_index"]["issued_at"] == iso(seeing_provider.run)
        service.tick(now+timedelta(minutes=5), force=False)
        assert len(adapter.alerts) == 2 and detector.ephemeris.calls == 1
        assert forecast.calls == 1 and len(seeing_provider.point_calls) == 2
        with TestClient(create_app(service, background=False), base_url="http://localhost") as client:
            event_id = predictions[0]["candidate"]["id"]
            detail = client.get(f"/events/{event_id}")
            assert detail.status_code == 200
            assert "Moon–planet occultation contacts" in detail.text
            assert "Disappearance begins" in detail.text and "Reappearance complete" in detail.text
            assert "physical timing uncertainty" in detail.text
        assert forecast.calls == 1 and detector.ephemeris.calls == 1
        message = render_message({"id": "fixture", "stage": "ready", "prediction": predictions[0]}, "fixture@example.org", "America/New_York")
        text = message.get_content()
        assert "Nominal Moon–planet occultation contacts" in text and "EDT" in text
        assert "Canadian seeing: 5" in text and "actual prediction accuracy" in text
    finally:
        service.close()


def test_daytime_opt_in_reuses_solar_safety(config, store, conditions, now):
    ephemeris = FixtureEphemeris(now)
    original = ephemeris.geometry
    ephemeris.geometry = lambda t: {**original(t), "sun_altitude_deg": 10}
    detector, batch = fixture_detection(config, now, ephemeris, maximum_sun_altitude_deg=90)
    assert len(batch.candidates) == 2 and all(e.metadata["safety"] for e in batch.candidates)
    service = Service(config, store=store, conditions=FakeConditions(conditions), detectors=[detector], sources={}, adapters=[], clock=lambda: now)
    try:
        service.tick(now)
        prediction = service.upcoming()[0]
        text = render_message({"id": "daylight", "stage": "ready", "prediction": prediction}, "fixture@example.org").get_content()
        assert "SOLAR VIEWING SAFETY" in text and "front-aperture" in text
    finally:
        service.close()


def test_disappearance_and_reappearance_use_their_own_cloud_forecasts(config, store, conditions, now):
    detector, _ = fixture_detection(config, now)
    initial_calls = detector.ephemeris.calls
    cloudy = replace(conditions, metrics={**conditions.metrics,
                     "cloud_total": replace(conditions.metrics["cloud_total"], value=100)})
    class ContactConditions(FakeConditions):
        reappearance_clear = False
        def at(self, when, geometry):
            self.requests.append(when)
            return conditions if self.reappearance_clear or when < detector.ephemeris.maximum else cloudy
    shared = ContactConditions(conditions)
    adapter = RecordingAdapter()
    service = Service(config, store=store, conditions=shared, detectors=[detector], sources={},
                      adapters=[adapter], clock=lambda: now)
    try:
        service.tick(now, force=False)
        assert len(adapter.alerts) == 1
        predictions = {p["candidate"]["metadata"]["transition"]: p for p in service.upcoming()}
        assert predictions["disappearance"]["assessment"]["quality"] > 55
        assert predictions["reappearance"]["assessment"]["quality"] == 0
        shared.reappearance_clear = True
        service.tick(now+timedelta(minutes=5), force=False)
        assert len(adapter.alerts) == 2
        assert detector.ephemeris.calls == initial_calls+1  # Weather updates do not recalculate contacts.
    finally:
        service.close()


def test_long_range_geometry_can_appear_in_weekly_with_unknown_weather(config, store, weather, now):
    config.values["detectors"]["lunar_occultations"].update(enabled=True, horizon_days=30, bodies=["Saturn"])
    config.values["gmail"]["weekly_digest"].update(enabled=True, event_types=["lunar_occultations"])
    forecast = FakeProvider(weather)
    conditions = ConditionsService(store, forecast, config.observer, config.values["atmosphere"])
    detector = LunarOccultationDetector(config.values["detectors"]["lunar_occultations"], FixtureEphemeris(now+timedelta(days=10)))
    service = Service(config, store=store, conditions=conditions, detectors=[detector], sources={}, adapters=[], clock=lambda: now)
    try:
        service.tick(now)
        predictions = service.upcoming()
        assert len(predictions) == 2
        assert all(p["assessment"]["quality"] is None for p in predictions)
        payload = build(config, store, "weekly_digest", now.date(), now)
        assert len(payload["items"]) == 2
        assert all(i["quality"] is None for i in payload["items"])
        assert forecast.calls == 1
    finally:
        service.close()


def test_configuration_registry_and_coverage_guards(tmp_path):
    raw = {"observer": {"latitude": 40, "longitude": -74, "timezone": "America/New_York"}}
    assert not from_dict(raw, tmp_path).values["detectors"]["lunar_occultations"]["enabled"]
    assert DETECTORS["lunar_occultations"].profiles == ("planetary",)
    assert DETECTORS["lunar_occultations"].long_range_horizon == "horizon_days"
    assert "detectors.lunar_occultations.enabled" in field_definitions()
    assert load(Path(__file__).parents[1]/"config.example.toml").values["detectors"]["lunar_occultations"]["enabled"]
    for key, value in (("bodies", ["Sun"]), ("bodies", ["Mars", "Mars"]), ("horizon_days", True),
                       ("horizon_days", 367), ("minimum_solar_elongation_deg", 0), ("preparation_minutes", 0)):
        with pytest.raises(ValueError):
            from_dict({**raw, "detectors": {"lunar_occultations": {key: value}}}, tmp_path)
    ephemeris = LunarOccultationEphemeris()
    with pytest.raises(ValueError, match="coverage"):
        list(ephemeris.occultations("Mars", parse("2054-01-01T00:00:00Z"), parse("2054-01-03T00:00:00Z"), Observer(0, 0, 0, "UTC")))
