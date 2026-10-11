from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
import time
from fastapi.testclient import TestClient

from all_sky_alert.assessment import planetary, sunset
from all_sky_alert.canadian import CanadianSeeing, GeoMetSeeing
from all_sky_alert.config import load
from all_sky_alert.domain import DetectionBatch, Metric, iso
from all_sky_alert.notifications import render_message
from all_sky_alert.policy import qualified
from all_sky_alert.providers import ConditionsService
from all_sky_alert.service import Service
from all_sky_alert.web import create_app
from conftest import FakeProvider, RecordingAdapter


class SeeingProvider:
    def __init__(self, now, value=4):
        self.run, self.value = now-timedelta(hours=6), value
        self.calls, self.point_calls = 0, []
        self.error, self.point_error = None, None

    def capabilities(self):
        self.calls += 1
        if self.error:
            raise self.error
        return {"run": iso(self.run), "start": iso(self.run+timedelta(hours=1)),
                "end": iso(self.run+timedelta(hours=84))}

    def point(self, observer, run, valid, max_distance):
        self.point_calls.append((run, valid))
        if self.point_error:
            raise self.point_error
        return {"value": self.value, "run": iso(run), "valid": iso(valid),
                "grid": [observer.longitude, observer.latitude]}


def seeing_service(store, config, provider):
    return CanadianSeeing(store, provider, config.observer,
                          {**config.values["atmosphere"]["canadian"], "enabled": True})


def test_shared_hour_cache_restart_and_no_io_at(store, config, now):
    provider = SeeingProvider(now)
    service = seeing_service(store, config, provider)
    service.refresh(now)
    times = [now+timedelta(hours=3, minutes=m) for m in (0, 10, 20)]
    service.prepare(times*5)
    for when in times:
        metric = service.at(when, {}).metrics["seeing_index"]
        assert metric.value == 4 and metric.issued_at == iso(provider.run)
        assert metric.valid_at == iso(times[0])
    restarted = seeing_service(store, config, provider)
    restarted.refresh(now+timedelta(minutes=5))
    restarted.prepare(times)
    assert restarted.at(times[0], {}).metrics["seeing_index"].value == 4
    assert provider.calls == 1 and len(provider.point_calls) == 1


def test_new_model_and_observer_separate_point_cache(store, config, now):
    provider = SeeingProvider(now)
    service = seeing_service(store, config, provider)
    service.refresh(now)
    service.prepare([now+timedelta(hours=3)])
    provider.run += timedelta(hours=6)
    provider.value = 1
    service.refresh(now+timedelta(hours=1))
    service.prepare([now+timedelta(hours=3)])
    assert service.at(now+timedelta(hours=3), {}).metrics["seeing_index"].value == 1
    other = CanadianSeeing(store, provider, replace(config.observer, longitude=1), service.settings)
    other.refresh(now+timedelta(hours=1))
    other.prepare([now+timedelta(hours=3)])
    assert len(provider.point_calls) == 3


def test_horizon_no_extrapolation_and_run_age(store, config, now):
    provider = SeeingProvider(now)
    service = seeing_service(store, config, provider)
    service.refresh(now)
    end = provider.run+timedelta(hours=84)
    service.prepare([end, end+timedelta(seconds=1)])
    assert service.at(end, {}).metrics["seeing_index"].value == 4
    assert service.at(end+timedelta(seconds=1), {}).status == "outside_forecast"
    service.refresh(now+timedelta(hours=13))  # run is 19h old, despite new retrieval
    assert service.status == "unavailable"
    assert not service.at(end, {}).metrics


def test_stale_seeing_does_not_veto_sunset_but_blocks_routine_planet_alert(store, config, now, event, conditions):
    provider = SeeingProvider(now, 5)
    service = seeing_service(store, config, provider)
    service.refresh(now)
    service.prepare([event.peak])
    provider.error = httpx.ConnectError("private remote body")
    service.refresh(now+timedelta(hours=2))
    service.prepare([event.peak])
    extra = service.at(event.peak, {})
    assert extra.metrics["seeing_index"].status == "stale"
    combined = replace(conditions, metrics={**conditions.metrics, **extra.metrics})
    assessment = planetary(event, combined)
    assert not assessment.usable_for_alerts
    assert not qualified(event, assessment, {**config.policy(event.kind), "minimum_confidence": 0, "minimum_quality": 0})
    assert qualified(replace(event, interest=100), assessment, config.policy(event.kind))
    assert sunset(event, combined).quality == sunset(event, conditions).quality
    assert sunset(event, combined).confidence == sunset(event, conditions).confidence
    assert sunset(event, combined).usable_for_alerts
    assert "private" not in str(store.cache(service.key))
    restarted = seeing_service(store, config, provider)
    restarted.refresh(now+timedelta(hours=2, seconds=10))
    assert provider.calls == 2
    service.refresh(now+timedelta(hours=7))
    assert not service.at(event.peak, {}).metrics


@pytest.mark.parametrize("value", [0, 1, 2, 3, 4, 5])
def test_native_index_precedence_mask_and_independent_interest(value, event, conditions, now):
    index = Metric(value, "index", .75, "ECCC RDPS", "native category", iso(now))
    augmented = replace(conditions, metrics={**conditions.metrics, "seeing_index": index})
    assessment = planetary(event, augmented)
    assert event.interest == 70
    assert "Canadian seeing suitability" in assessment.components
    if value == 0:
        assert assessment.quality is None
        assert any("masked" in reason for reason in assessment.reasons)
    else:
        assert assessment.components["Canadian seeing suitability"] == (value-1)*25
    # Canadian seeing does not influence the cloud-layer sunset score.
    assert sunset(event, augmented).quality == sunset(event, conditions).quality


def test_physical_seeing_can_take_precedence(event, conditions, now):
    metrics = {**conditions.metrics, "seeing_index": Metric(1, "index", .7, "ECCC", "native", iso(now)),
               "seeing_arcsec": Metric(.5, "arcsec", .9, "fixture", "physical", iso(now))}
    assert planetary(event, replace(conditions, metrics=metrics)).components["seeing suitability"] == 100


def test_partial_point_failure_does_not_erase_other_hours(store, config, now):
    provider = SeeingProvider(now)
    service = seeing_service(store, config, provider)
    service.refresh(now)
    service.prepare([now+timedelta(hours=1)])
    provider.point_error = ValueError("malformed point")
    service.prepare([now+timedelta(hours=2)])
    assert service.at(now+timedelta(hours=1), {}).metrics["seeing_index"].value == 4
    assert not service.at(now+timedelta(hours=2), {}).metrics
    assert next(h for h in store.health_rows() if h["name"] == "Canadian seeing")["status"] == "degraded"


def test_429_shared_backoff_survives_restart(store, config, now):
    provider = SeeingProvider(now)
    request = httpx.Request("GET", GeoMetSeeing.url)
    response = httpx.Response(429, headers={"Retry-After": "1800"}, request=request)
    provider.point_error = httpx.HTTPStatusError("do not store body", request=request, response=response)
    service = seeing_service(store, config, provider)
    service.refresh(now)
    service.prepare([now+timedelta(hours=1), now+timedelta(hours=2)])
    restarted = seeing_service(store, config, provider)
    restarted.refresh(now+timedelta(minutes=10))
    restarted.prepare([now+timedelta(hours=3)])
    assert len(provider.point_calls) == 1
    assert "body" not in str(store.cache(service.gate))


def test_missing_point_and_disabled_master_weather(store, config, weather, now):
    provider = SeeingProvider(now, None)
    seeing = seeing_service(store, config, provider)
    seeing.refresh(now)
    seeing.prepare([now])
    assert not seeing.at(now, {}).metrics
    weather_provider = FakeProvider(weather)
    service = ConditionsService(store, weather_provider, config.observer,
        {**config.values["atmosphere"], "enabled": False}, seeing=seeing)
    service.refresh(now)
    service.prepare([now])
    assert service.at(now, {}).status == "disabled"
    assert not service.at(now, {}).metrics and weather_provider.calls == 0


def test_weather_failure_does_not_stop_seeing(store, config, weather, now):
    weather_provider = FakeProvider(weather)
    weather_provider.error = httpx.ConnectError("offline")
    seeing = seeing_service(store, config, SeeingProvider(now))
    service = ConditionsService(store, weather_provider, config.observer, config.values["atmosphere"], seeing=seeing)
    service.refresh(now)
    service.prepare([now])
    snapshot = service.at(now, {})
    assert snapshot.status == "unavailable" and snapshot.metrics["seeing_index"].value == 4
    assert snapshot.additional_sources[0].name == "Environment and Climate Change Canada"


def test_point_budget_defers_download_but_preserves_cached_samples(store, config, now):
    provider = SeeingProvider(now)
    service = seeing_service(store, config, provider)
    service.refresh(now)
    service.prepare([now])
    restarted = seeing_service(store, config, provider)
    restarted.refresh(now+timedelta(minutes=5))
    restarted.deadline = time.monotonic()-1
    restarted.prepare([now, now+timedelta(hours=1)])
    assert restarted.at(now, {}).metrics["seeing_index"].value == 4
    assert not restarted.at(now+timedelta(hours=1), {}).metrics
    assert len(provider.point_calls) == 1


def test_broken_supplementary_provider_preserves_weather(store, config, weather, now):
    class BrokenSeeing:
        def refresh(self, now):
            raise RuntimeError("unexpected failure")
        def prepare(self, times):
            raise RuntimeError("unexpected failure")
        def at(self, when, geometry):
            raise RuntimeError("unexpected failure")
    service = ConditionsService(store, FakeProvider(weather), config.observer,
                                config.values["atmosphere"], seeing=BrokenSeeing())
    service.refresh(now)
    service.prepare([now])
    assert service.at(now, {}).metrics["cloud_total"].value == 5
    assert service.at(now, {}).status == "fresh"


@pytest.mark.parametrize("bad", [True, -1, 6, 2.5, float("nan"), "4"])
def test_untrusted_category_rejected(config, now, bad):
    data = SeeingProvider(now, bad).point(config.observer, now, now+timedelta(hours=1), 25)
    with pytest.raises(ValueError):
        GeoMetSeeing.validate_point(data, config.observer, now, now+timedelta(hours=1), 25)


@pytest.mark.parametrize("mutation", ["distance", "run", "time", "coordinates"])
def test_wrong_grid_run_or_time_rejected(config, now, mutation):
    data = SeeingProvider(now).point(config.observer, now, now+timedelta(hours=1), 25)
    if mutation == "distance":
        data["grid"][1] += 2
    elif mutation == "run":
        data["run"] = iso(now-timedelta(hours=6))
    elif mutation == "time":
        data["valid"] = iso(now+timedelta(hours=2))
    else:
        data["grid"] = ["0", 51]
    with pytest.raises(ValueError):
        GeoMetSeeing.validate_point(data, config.observer, now, now+timedelta(hours=1), 25)


def test_real_geomet_http_contract_and_cloud_mask(config, now):
    end = now+timedelta(hours=84)
    xml = f'''<WMS_Capabilities xmlns="http://www.opengis.net/wms"><Capability><Layer><Layer>
      <Name>{GeoMetSeeing.layer}</Name><Dimension name="time">{iso(now+timedelta(hours=1))}/{iso(end)}/PT1H</Dimension>
      <Dimension name="reference_time" default="{iso(now)}"/></Layer></Layer></Capability></WMS_Capabilities>'''
    def handler(request):
        p = request.url.params
        assert p["VERSION"] == "1.3.0" and p["LAYERS"] == GeoMetSeeing.layer
        if p["REQUEST"] == "GetCapabilities":
            return httpx.Response(200, text=xml)
        assert p["TIME"] == iso(now+timedelta(hours=1)).replace("+00:00", "Z")
        assert p["DIM_REFERENCE_TIME"] == iso(now).replace("+00:00", "Z")
        assert p["WIDTH"] == "101" and p["I"] == "50" and p["CRS"] == "EPSG:4326"
        lat, lon = config.observer.latitude, config.observer.longitude
        assert p["BBOX"] == f"{lat-.5},{lon-.5},{lat+.5},{lon+.5}"
        return httpx.Response(200, json={"type": "FeatureCollection", "layer": GeoMetSeeing.layer,
            "features": [{"geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"value": 0, "time": p["TIME"], "dim_reference_time": p["DIM_REFERENCE_TIME"]}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = GeoMetSeeing(client)
        assert provider.capabilities()["run"] == iso(now)
        assert provider.point(config.observer, now, now+timedelta(hours=1), 25)["value"] == 0


@pytest.mark.parametrize("body", [b"<!DOCTYPE root><root/>", b"x"*100001, b"<broken"],
                         ids=["doctype", "oversized", "malformed"])
def test_unsafe_oversized_or_malformed_xml_rejected(body):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))) as client:
        with pytest.raises(Exception):
            GeoMetSeeing(client).capabilities()


def test_service_update_dedup_ui_and_email_provenance(store, config, weather, now, event):
    provider = SeeingProvider(now, 1)
    seeing = seeing_service(store, config, provider)
    conditions = ConditionsService(store, FakeProvider(weather), config.observer, config.values["atmosphere"], seeing=seeing)
    class Detector:
        name = "planetary"
        def detect(self, ctx):
            return DetectionBatch([event], {event.scope})
    adapter = RecordingAdapter()
    service = Service(config, store=store, conditions=conditions, detectors=[Detector()], adapters=[adapter], clock=lambda: now)
    try:
        service.tick(now)
        assert not adapter.alerts
        provider.run += timedelta(hours=6)
        provider.value = 5
        service.tick(now+timedelta(hours=1))
        assert len(adapter.alerts) == 1
        service.tick(now+timedelta(hours=1, minutes=5))
        assert len(adapter.alerts) == 1
        assert len(store.detail(event.id, config.observer.key)["revisions"]) >= 2
        message = render_message(adapter.alerts[0], "recipient@example.org").get_content()
        assert "Canadian seeing" in message and "Environment and Climate Change Canada" in message
        with TestClient(create_app(service, background=False), base_url="http://localhost") as client:
            home = client.get("/").text
            detail = client.get(f"/events/{event.id}").text
            assert "Excellent (5/5)" in home
            assert "Model run" in detail and "ECCC RDPS" in detail and "ECCC Data Services End-use Licence" in detail
            prediction = client.get(f"/api/events/{event.id}").json()
            assert prediction["assessment"]["conditions"]["metrics"]["seeing_index"]["issued_at"] == iso(provider.run)
    finally:
        service.close()


@pytest.mark.parametrize("setting", ['enabled="true"', 'max_run_age_hours=0', 'max_grid_distance_km=100', 'cache_minutes=0'])
def test_canadian_config_validation(tmp_path, setting):
    path = tmp_path/"config.toml"
    path.write_text('[observer]\nlatitude=0\nlongitude=0\ntimezone="UTC"\n[atmosphere.canadian]\n'+setting)
    with pytest.raises(ValueError):
        load(path)


def test_prune_superseded_point_cache(store, now):
    store.cache_success("eccc-point/old", {}, now-timedelta(days=8))
    store.cache_failure("eccc-point/failed", 1, now-timedelta(days=8), "ValueError")
    store.cache_success("eccc-point/current", {}, now)
    store.prune(now, 90)
    assert store.cache("eccc-point/old") is None and store.cache("eccc-point/failed") is None
    assert store.cache("eccc-point/current") is not None
