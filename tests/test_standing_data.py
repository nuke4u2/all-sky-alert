"""Synthetic CC0-shaped catalogs; no live feed, upstream download or real mail."""
from dataclasses import asdict, replace
from datetime import timedelta
import io
import json
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi.testclient import TestClient
import httpx
import pytest
import tomlkit

from all_sky_alert.adsb import AircraftState
from all_sky_alert.aircraft_display import aircraft_summary
from all_sky_alert.config import from_dict, DEFAULTS as CONFIG_DEFAULTS
from all_sky_alert.config_edit import ConfigEditor
from all_sky_alert.domain import iso
from all_sky_alert.enrichment import DEFAULTS
from all_sky_alert.service import Service
from all_sky_alert.sources import build_sources
from all_sky_alert.standing_data import StandingDataEnrichment, callsign_key, import_archive, ROUTE_NOTE
from all_sky_alert.notifications import render_message
from all_sky_alert.web import create_app
from conftest import FakeConditions


def archive_bytes(overrides=None):
    files = {
        "LICENSE": "Creative Commons Legal Code\nCC0 1.0 Universal\nSynthetic fixture",
        "aircraft/schema-01/A/AB/ABC.csv": "ICAO,ModelICAO,Manufacturer,Model\nABC123,A320,Airbus,A320 fixture variant\n",
        "airlines/schema-01/airlines.csv": "Code,ICAO,Name\nJBU,JBU,Fixture Air\n",
        "airports/schema-01/K/KJ.csv": "Code,IATA,Name\nKJFK,JFK,Fixture Origin\nKBOS,BOS,Fixture Destination\n",
        "model-type/schema-01/C.csv": "ICAO,Manufacturer,Model,IsActive\nC172,Cessna,172,1\nC172,Cessna,172,0\nA20N,Airbus,A320neo variant one,1\nA20N,Airbus,A320neo variant two,1\n",
        "routes/schema-01/J/JBU-all.csv": "Callsign,AirlineCode,AirportCodes\nJBU123,JBU,KJFK-KBOS\nJBU456,JBU,KJFK-KATL-KBOS\nJBU999,JBU,KJFK-XXXX\n",
    }
    files.update(overrides or {})
    data = io.BytesIO()
    with ZipFile(data, "w", ZIP_DEFLATED) as zipped:
        for name, content in files.items():
            if content is not None:
                zipped.writestr("standing-data-main/"+name, "\ufeff"+content if name.endswith(".csv") else content)
    return data.getvalue()


class Positions:
    settings = {"stale_seconds": 30}
    def __init__(self, now):
        self.states = [AircraftState("abc123", iso(now), 0, 0, 2000, "geometric", 100, 90, None, "JBU123", "A320")]
    def snapshot(self, now):
        return self.states, {}


def make_catalog(store, config, now, handler=None):
    requests = []
    def serve(request):
        requests.append(request)
        return handler(request) if handler else httpx.Response(200, content=archive_bytes(), headers={"ETag": '"fixture-v1"'})
    client = httpx.Client(transport=httpx.MockTransport(serve))
    positions = Positions(now)
    catalog = StandingDataEnrichment(client, store, config.observer, {**DEFAULTS, "enabled": True, "mode": "vrs"}, ["adsb"], positions)
    return catalog, positions, requests, client


@pytest.mark.parametrize("raw,expected", [("JBU123", "JBU123"), (" jbu00123 ", "JBU123"),
    ("EZY00AB", "EZY0AB"), ("JBU0000", "JBU0"), ("N12345", None), ("B6123", None), ("ABC12345", None)])
def test_callsign_normalization_is_bounded_and_icao_only(raw, expected):
    assert callsign_key(raw) == expected


def test_weekly_conditional_cache_and_restart_no_per_aircraft_requests(store, config, now):
    catalog, positions, requests, client = make_catalog(store, config, now)
    try:
        catalog.refresh(now)  # Live lane never downloads, even on a cold cache.
        assert not requests and catalog.at(positions.states[0], now) is None
        catalog.maintain(now)
        catalog.refresh(now)
        row = catalog.at(positions.states[0], now)
        assert row["airline"] == "Fixture Air" and row["manufacturer"] == "Airbus"
        assert row["origin"].startswith("JFK") and row["destination"].startswith("BOS")
        assert row["route_note"] == ROUTE_NOTE and "CC0" in row["rights"]
        for i in range(10):
            catalog.refresh(now+timedelta(seconds=i))
            assert catalog.at(positions.states[0], now+timedelta(seconds=i))
            catalog.maintain(now+timedelta(seconds=i))
        assert len(requests) == 1
        restarted = StandingDataEnrichment(client, store, config.observer, catalog.settings, ["adsb"], positions)
        restarted.maintain(now+timedelta(days=1))
        assert len(requests) == 1
        client._transport = httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(304))
        restarted.maintain(now+timedelta(days=8))
        assert requests[-1].headers["If-None-Match"] == '"fixture-v1"'
        positions.states = [replace(positions.states[0], position_at=iso(now+timedelta(days=8)))]
        restarted.refresh(now+timedelta(days=8))
        assert restarted.at(positions.states[0], now+timedelta(days=8))["updated_at"] == iso(now+timedelta(days=8))
        assert len(requests) == 2
    finally:
        client.close()


def test_at_is_io_free_and_component_expiry_preserves_static_labels(store, config, now, monkeypatch):
    catalog, positions, requests, client = make_catalog(store, config, now)
    try:
        catalog.maintain(now);catalog.refresh(now)
        state = positions.states[0]
        prepared = catalog.at(state, now)
        monkeypatch.setattr(catalog, "_connection", lambda: pytest.fail("Snapshot read touched SQLite"))
        monkeypatch.setattr(client, "stream", lambda *a, **k: pytest.fail("Snapshot read touched network"))
        later = now+timedelta(days=15)
        row = catalog.at(state, later)
        assert row["airline"] == "Fixture Air" and "origin" not in row
        summary = aircraft_summary({"enrichment": prepared, "type_code": "A320"}, later)
        assert summary["airline"] == "Fixture Air" and summary["origin"] == "Unknown origin"
        assert catalog.at(state, now+timedelta(days=31)) is None
        assert len(requests) == 1
    finally:
        client.close()


def test_callsign_changes_multistop_ambiguity_type_conflicts_and_stale_positions(store, config, now):
    catalog, positions, _, client = make_catalog(store, config, now)
    try:
        catalog.maintain(now);catalog.refresh(now)
        original = positions.states[0]
        assert catalog.at(replace(original, callsign="JBU456"), now) is None
        positions.states = [replace(original, callsign="JBU456"),
                            replace(original, icao24="def456", callsign="JBU999", type_code="A20N"),
                            replace(original, icao24="aaaaaa", callsign="N12345", type_code="C172"),
                            replace(original, icao24="bbbbbb", position_at=iso(now-timedelta(seconds=31)))]
        catalog.refresh(now)
        multi = catalog.at(positions.states[0], now)
        assert multi["airline"] == "Fixture Air" and "origin" not in multi
        ambiguous = catalog.at(positions.states[1], now)
        assert "type_name" not in ambiguous and "origin" not in ambiguous
        private = catalog.at(positions.states[2], now)
        assert private["type_name"] == "Cessna 172" and "airline" not in private
        assert catalog.at(positions.states[3], now) is None
    finally:
        client.close()


@pytest.mark.parametrize("failure", ["badzip", "badlicense", "schema", "oversized", "429", "503", "space"])
def test_bad_updates_keep_last_good_database_and_backoff(store, config, now, monkeypatch, failure):
    catalog, positions, requests, client = make_catalog(store, config, now)
    try:
        catalog.maintain(now)
        before = catalog.path.read_bytes()
        def failed(request):
            requests.append(request)
            if failure in ("429", "503"):
                return httpx.Response(int(failure), headers={"Retry-After": "7200"})
            if failure == "oversized":
                return httpx.Response(200, content=b"x", headers={"Content-Length": str(20*1024*1024)})
            content = b"invalid zip" if failure == "badzip" else archive_bytes({"LICENSE": "not the expected rights"}) if failure == "badlicense" else archive_bytes({"airlines/schema-01/airlines.csv": "wrong\nschema\n"}) if failure == "schema" else archive_bytes()
            return httpx.Response(200, content=content)
        client._transport = httpx.MockTransport(failed)
        if failure == "space":
            monkeypatch.setattr("all_sky_alert.standing_data.shutil.disk_usage", lambda *a: type("Disk", (), {"free": 1})())
        later = now+timedelta(days=8)
        catalog.maintain(later)
        assert catalog.path.read_bytes() == before
        cache = store.cache(catalog.key)
        assert cache["error"] and cache["next_attempt"]
        attempts = len(requests)
        catalog.maintain(later+timedelta(minutes=1))
        assert len(requests) == attempts
        positions.states = [replace(positions.states[0], position_at=iso(later))]
        catalog.refresh(later)
        assert catalog.at(positions.states[0], later)
        assert catalog.health.view(later)["status"] == "stale"
    finally:
        client.close()


def test_import_rejects_zip_paths_row_budgets_and_incomplete_data(tmp_path, now, monkeypatch):
    meta = {"verified_at": iso(now), "sha256": "fixture"}
    for i, data in enumerate((archive_bytes({"../escape.csv": "bad"}),
                              archive_bytes({"airlines/schema-01/airlines.csv": None}))):
        with pytest.raises(ValueError):
            import_archive(io.BytesIO(data), tmp_path/f"bad-{i}.sqlite3", meta)
    monkeypatch.setattr("all_sky_alert.standing_data.ROW_LIMIT", 2)
    with pytest.raises(ValueError, match="budget"):
        import_archive(io.BytesIO(archive_bytes()), tmp_path/"bounded.sqlite3", meta)
    assert not (tmp_path.parent/"escape.csv").exists()


def test_settings_modes_legacy_compatibility_and_atomic_save(tmp_path):
    raw = {"observer": {"latitude": 0, "longitude": 0, "timezone": "UTC"}}
    legacy = from_dict(raw, tmp_path)
    assert legacy.values["providers"]["enrichment"]["mode"] == "local"
    assert not legacy.values["providers"]["enrichment"]["enabled"]
    for changes in ({"mode": "bad"}, {"refresh_days": 0}, {"routes_max_age_days": 3}, {"metadata_max_age_days": 1}):
        with pytest.raises(ValueError):
            from_dict({**raw, "providers": {"enrichment": changes}}, tmp_path)
    path = tmp_path/"private.toml"
    path.write_text('[observer]\nlatitude=0\nlongitude=0\ntimezone="UTC"\n')
    editor = ConfigEditor(path)
    _, revision, _ = editor.read()
    saved, _ = editor.save({"providers.enrichment.enabled": True, "providers.enrichment.mode": "vrs"}, revision)
    assert saved.values["providers"]["enrichment"]["mode"] == "vrs"
    assert not (tmp_path/"aircraft-standing.sqlite3").exists()  # Saving config acquires nothing.


def test_service_bulk_maintenance_slow_only_and_labels_ui_mail(store, config, conditions, now):
    config.values["providers"]["enrichment"].update(enabled=True, mode="vrs")
    path = config.base/"private-ui.toml"
    path.write_text(tomlkit.dumps({"observer": asdict(config.observer), **config.values}), encoding="utf-8")
    config = replace(config, file_path=path)
    requests = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(200, content=archive_bytes()))) as client:
        sources = build_sources(client, store, config.observer, config.values["providers"], {"adsb": ["adsb"], "enrichment": ["adsb"]}, config.base)
        catalog = sources["enrichment"]
        positions = Positions(now)
        catalog.positions = positions
        class Detector:
            name = "adsb"
            def detect(self, context):
                from all_sky_alert.domain import DetectionBatch
                return DetectionBatch([], set())
        service = Service(config, store=store, conditions=FakeConditions(conditions), detectors=[Detector()],
                          sources={"enrichment": catalog}, adapters=[], clock=lambda: now)
        try:
            service.tick(now, lane="live")
            assert requests == []
            service.tick(now, lane="slow")
            assert len(requests) == 1
            service.tick(now, lane="live")
            row = catalog.at(positions.states[0], now)
            assert row["airline"] == "Fixture Air"
            with TestClient(create_app(service, background=False), base_url="http://localhost") as web:
                page = web.get("/settings")
                assert page.status_code == 200 and "providers.enrichment.mode" in page.text
            assert len(requests) == 1
        finally:
            service.close()


def test_public_enrichment_in_existing_aircraft_detector_and_notifications(store, config, now, conditions, event):
    from all_sky_alert.adsb import ADSBLol
    from all_sky_alert.aircraft import AircraftDetector
    from all_sky_alert.assessment import aircraft
    from dataclasses import asdict
    radius = {"now": now.timestamp()*1000, "ac": [{"hex": "abc123", "lat": config.observer.latitude,
        "lon": config.observer.longitude, "alt_geom": 10000, "seen_pos": 0, "flight": "JBU123", "t": "A320"}]}
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=radius))) as feed_client:
        positions = ADSBLol(feed_client, store, config.observer, CONFIG_DEFAULTS["providers"]["adsb"], ["adsb"])
        catalog, _, _, client = make_catalog(store, config, now)
        catalog.positions = positions
        try:
            positions.refresh(now);catalog.maintain(now);catalog.refresh(now)
            candidate, = AircraftDetector(CONFIG_DEFAULTS["detectors"]["adsb"], positions, catalog).detect(
                __import__('all_sky_alert.domain', fromlist=['DetectionContext']).DetectionContext(config.observer, now, 1, None)).candidates
            assert candidate.metadata["enrichment"]["route_note"] == ROUTE_NOTE
            assert "unconfirmed" in candidate.reasons[0]
            payload = {"candidate": candidate.to_dict(), "assessment": asdict(aircraft(candidate, conditions)), "state": "ACTIVE", "priority": 70}
            message = render_message({"id": "fixture", "stage": "active", "prediction": payload}, "fixture@example.org")
            assert ROUTE_NOTE in message.get_content()
            assert "Fixture Air" in str(message["Subject"])
            assert "Catalog route (unconfirmed)" in str(message["Subject"])
        finally:
            client.close()
