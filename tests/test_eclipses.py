import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from all_sky_alert.astronomy import Astronomy
from all_sky_alert.assessment import eclipse
from all_sky_alert.domain import Conditions, DetectionContext, Observer, parse
from all_sky_alert.eclipse import EclipseDetector
from all_sky_alert.notifications import render_message
from all_sky_alert.policy import alert_stages
from all_sky_alert.service import Service
from all_sky_alert.web import create_app
from conftest import FakeConditions


def calculate(observer, when, **settings):
    detector = EclipseDetector({**EclipseDetector.defaults, "horizon_days":2, **settings})
    return detector.detect(DetectionContext(observer, when, 1, Astronomy())).candidates


def test_dallas_contacts_against_independent_usno_reference():
    reference = json.loads((Path(__file__).parent/"fixtures/usno-dallas-2024.json").read_text())
    observer = Observer(*reference["observer"], "America/Chicago")
    event, = calculate(observer, datetime(2024,4,7,tzinfo=UTC), lunar=False)
    assert event.kind == "eclipse.solar.total"
    assert event.metadata["maximum_visible"] and event.metadata["maximum_obscuration"] == 1
    for contact in event.metadata["contacts"]:
        expected = parse(reference["date"]+"T"+reference["contacts"][contact["phase"]]+"Z")
        assert abs((parse(contact["time"])-expected).total_seconds()) < 60
        assert contact["visible"]
    assert event.interest == 100
    assert "front-aperture" in event.metadata["safety"]["text"]


@pytest.mark.parametrize("observer,when,kind", [
    (Observer(40.7128,-74.006,10,"America/New_York"), datetime(2024,4,7,tzinfo=UTC), "partial"),
    (Observer(35.0844,-106.6504,1619,"America/Denver"), datetime(2023,10,13,tzinfo=UTC), "annular"),
])
def test_observer_local_solar_types(observer, when, kind):
    event, = calculate(observer, when, lunar=False)
    assert event.kind == "eclipse.solar."+kind
    assert event.metadata["visible_kind"] == kind


@pytest.mark.parametrize("observer,kind,maximum_visible", [
    (Observer(40.7128,-74.006,10,"America/New_York"), "total", True),
    (Observer(51.4779,0,46,"Europe/London"), "partial", False),
    (Observer(31.23,121.47,10,"Asia/Shanghai"), None, False),
])
def test_lunar_totality_clipped_to_local_rise_set(observer, kind, maximum_visible):
    events = calculate(observer, datetime(2025,3,13,tzinfo=UTC), solar=False)
    if kind is None:
        assert events == []
        return
    event, = events
    assert event.kind == "eclipse.lunar."+kind
    assert event.metadata["geometric_kind"] == "total"
    assert event.metadata["maximum_visible"] == maximum_visible
    assert event.geometry["geometric_altitude_deg"] >= -.01
    assert all(parse(v["start"]) < parse(v["end"]) for v in event.metadata["visible_intervals"])
    total = {c["phase"]:parse(c["time"]) for c in event.metadata["contacts"]}
    assert 64 < (total["total_end"]-total["total_begin"]).total_seconds()/60 < 68


def test_below_horizon_and_maximum_only_partly_visible(config, now):
    class Ephemeris:
        version = "fixture"
        def solar_eclipses(self, *args):
            return [{"body":"Sun", "kind":"total", "obscuration":1, "peak":now+timedelta(hours=2),
                     "contacts":[{"phase":p,"time":now+timedelta(minutes=m)} for p,m in
                        [("partial_begin",60),("total_begin",119),("peak",120),("total_end",121),("partial_end",180)]]}]
        def visible_intervals(self, *args):
            return self.intervals
        def position(self, *args):
            return {"altitude_deg":1, "azimuth_deg":270, "geometric_altitude_deg":.5}
    ephemeris = Ephemeris()
    detector = EclipseDetector({**EclipseDetector.defaults,"lunar":False,"horizon_days":2})
    context = DetectionContext(config.observer,now,1,ephemeris)
    ephemeris.intervals = []
    assert detector.detect(context).candidates == []
    ephemeris.intervals = [(now+timedelta(minutes=60),now+timedelta(minutes=110))]
    event, = detector.detect(context).candidates
    assert event.kind == "eclipse.solar.partial"
    assert not event.metadata["maximum_visible"]
    assert event.peak == event.end
    assert not next(c for c in event.metadata["contacts"] if c["phase"] == "total_begin")["visible"]


def test_penumbral_is_optional():
    observer = Observer(40.7128,-74.006,10,"America/New_York")
    when = datetime(2024,3,24,tzinfo=UTC)
    assert calculate(observer,when,solar=False,penumbral=False) == []
    event, = calculate(observer,when,solar=False,penumbral=True)
    assert event.kind == "eclipse.lunar.penumbral"


def test_only_penumbral_visible_from_global_total_respects_preference(config, now):
    class Sky:
        def lunar_eclipses(self,*args):
            return [{"body":"Moon","kind":"total","peak":now+timedelta(hours=3),"obscuration":1,
                     "contacts":[{"phase":phase,"time":now+timedelta(minutes=minute)} for phase,minute in
                        [("penumbral_begin",60),("partial_begin",120),("total_begin",170),("peak",180),
                         ("total_end",190),("partial_end",240),("penumbral_end",300)]]}]
        def visible_intervals(self,*args):
            return [(now+timedelta(minutes=60),now+timedelta(minutes=90))]
        def position(self,*args):
            return {"altitude_deg":1,"geometric_altitude_deg":.5}
    context = DetectionContext(config.observer,now,1,Sky())
    settings = {**EclipseDetector.defaults,"solar":False}
    assert EclipseDetector(settings).detect(context).candidates == []
    event, = EclipseDetector({**settings,"penumbral":True}).detect(context).candidates
    assert event.kind=="eclipse.lunar.penumbral" and not event.metadata["maximum_visible"]


def test_exceptional_cloudy_eclipse_and_far_horizon(config, conditions, now):
    # Separate horizon: requested eclipse lies beyond context.days=1.
    observer = Observer(32.7767,-96.797,130,"America/Chicago")
    when = datetime(2023,12,1,tzinfo=UTC)
    events = EclipseDetector({**EclipseDetector.defaults,"horizon_days":150,"lunar":False}).detect(
        DetectionContext(observer,when,1,Astronomy())).candidates
    event, = events
    cloudy = replace(conditions,metrics={**conditions.metrics,"cloud_total":replace(conditions.metrics["cloud_total"],value=100)})
    assessment = eclipse(event,cloudy)
    assert assessment.quality == 0 and event.interest == 100
    assert alert_stages(event,assessment,event.start-timedelta(days=3),config.policy(event.kind))
    assert eclipse(event,Conditions({},"unavailable",None,None)).quality is None


def test_eclipse_details_and_email_include_safety(config, store, conditions):
    when = datetime(2024,4,7,tzinfo=UTC)
    observer = Observer(32.7767,-96.797,130,"America/Chicago")
    cfg = replace(config,observer=observer)
    detector = EclipseDetector({**EclipseDetector.defaults,"horizon_days":2,"lunar":False})
    service = Service(cfg,store=store,conditions=FakeConditions(conditions),detectors=[detector],adapters=[],clock=lambda:when)
    try:
        service.tick(when)
        prediction, = service.upcoming()
        with TestClient(create_app(service,background=False),base_url="http://localhost") as client:
            detail = client.get("/events/"+prediction["candidate"]["id"])
            assert detail.status_code == 200
            assert "Local eclipse phases" in detail.text and "Solar viewing safety" in detail.text
            assert "Partial Begin" in detail.text and "NASA solar-viewing guidance" in detail.text
            assert "Apr 08 2024" in detail.text # Long-range events must display the year.
        message = render_message({"id":"test","stage":"watch","prediction":prediction},"fixture@example.org")
        assert "SOLAR VIEWING SAFETY" in message.get_content()
        assert "front-aperture" in message.get_content()
    finally:
        service.close()


def test_ephemeris_position_cache_returns_independent_dict(config, now):
    engine = Astronomy()
    original = engine.position("Sun",now,config.observer)
    original["altitude_deg"] = 999
    assert engine.position("Sun",now,config.observer)["altitude_deg"] != 999
    assert 0 <= engine.moon_illumination(now) <= 1
