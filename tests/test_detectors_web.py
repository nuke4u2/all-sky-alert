from dataclasses import replace
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from all_sky_alert.astronomy import Astronomy
from all_sky_alert.detectors import PlanetaryDetector, SunsetDetector, local_time
from all_sky_alert.domain import DetectionContext, Observer
from all_sky_alert.providers import ConditionsService
from all_sky_alert.service import Service
from all_sky_alert.web import create_app, safe_url
from conftest import FakeProvider


def test_real_geometry_and_common_weather_path(config, store, weather, now):
    provider = FakeProvider(weather)
    conditions = ConditionsService(store, provider, config.observer, config.values["atmosphere"])
    service = Service(config, store=store, conditions=conditions, adapters=[], clock=lambda: now)
    try:
        service.tick(now)
        predictions = service.upcoming()
        assert {p["candidate"]["detector"] for p in predictions} == {"planetary", "sunset"}
        assert provider.calls == 1
        for p in predictions:
            e = p["candidate"]
            assert e["start"] <= e["peak"] <= e["end"]
            assert e["temporal_kind"]=="opportunity"
            assert "version" in e["sources"][0]["method"]
            if e["detector"] == "planetary":
                assert e["geometry"]["altitude_deg"] >= 25
                assert 1 < e["metadata"]["diameter_arcsec"] < 100
        service.tick(now + timedelta(minutes=5))
        assert provider.calls == 1
    finally:
        service.close()


def test_malformed_or_unsafe_source_links_do_not_break_ui():
    for value in ("https://[", "javascript:alert(1)", "https://user:password@example.org/", "file:///secret"):
        assert safe_url(value) == "#"
    assert safe_url("https://example.org/source") == "https://example.org/source"


def test_daylight_transit_and_disjoint_windows(config, now):
    class FakeAstronomy:
        def position(self, body, when, observer):
            if body == "Sun":
                return {"altitude_deg": -12 if when.hour >= 18 or when.hour < 6 else 20}
            return {"altitude_deg": 80 if when.hour == 12 else 60 if when.hour == 22 else 30,
                    "azimuth_deg": 180, "diameter_arcsec": 40}
    settings = {**config.values["detectors"]["planetary"], "bodies": ["Jupiter"]}
    context = DetectionContext(replace(config.observer, timezone="UTC"), now, 1, FakeAstronomy())
    results = PlanetaryDetector(settings).detect(context)
    assert results.candidates
    assert all(e.peak.hour == 22 for e in results.candidates)
    first = results.candidates[0]
    assert first.start.hour == 18 and first.end.hour == 6


def test_polar_sunset_absence_and_dst(config, now):
    polar = Observer(89.0, 0, 0, "UTC")
    context = DetectionContext(polar, datetime(2026, 6, 20, tzinfo=UTC), 1, Astronomy())
    assert SunsetDetector().detect(context).candidates == []
    # Local noon-to-noon spans the fall DST change without naive timestamp arithmetic.
    site = Observer(40, -74, 0, "America/New_York")
    context = DetectionContext(site, now, 1, Astronomy())
    day = datetime(2026, 10, 31).date()
    assert (local_time(day + timedelta(days=1), 12, context) - local_time(day, 12, context)).total_seconds() == 25 * 3600


def test_html_json_details_health_escape_and_hosts(config, store, weather, now):
    conditions = ConditionsService(store, FakeProvider(weather), config.observer, config.values["atmosphere"])
    service = Service(config, store=store, conditions=conditions, adapters=[], clock=lambda: now)
    try:
        service.tick(now)
        with TestClient(create_app(service, background=False), base_url="http://localhost") as client:
            home = client.get("/")
            assert home.status_code == 200
            assert "<h2>Upcoming</h2>" in home.text and "<h2>Now</h2>" in home.text
            assert "Recent predictions" in home.text and "service health" in home.text
            assert "Open-Meteo" in home.text
            data = client.get("/api/predictions").json()
            id = data["upcoming"][0]["candidate"]["id"]
            detail = client.get(f"/events/{id}")
            assert detail.status_code == 200
            assert "Sources" in detail.text
            assert "Alerts &amp; delivery" in detail.text or "Alerts & delivery" in detail.text
            api_detail = client.get(f"/api/events/{id}").json()
            revision = api_detail["revisions"][0]["id"]
            assert client.get(f"/api/events/{id}/revisions/{revision}").status_code == 200
            assert client.get("/events/missing").status_code == 404
            assert client.get("/api/health").status_code == 200
            assert client.get("/", headers={"Host": "attacker.example"}).status_code == 400
            assert "script-src 'self'" in home.headers["Content-Security-Policy"]
            malicious = data["upcoming"][0]
            malicious["candidate"]["title"] = "<script>alert(1)</script>"
            malicious["candidate"]["sources"][0]["url"] = "javascript:alert(1)"
            store.save(malicious, now, [], [])
            escaped = client.get(f"/events/{id}")
            assert "<script>alert(1)</script>" not in escaped.text
            assert "&lt;script&gt;" in escaped.text
            assert 'href="javascript:' not in escaped.text
    finally:
        service.close()
