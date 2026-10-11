import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient

from all_sky_alert.assessment import aurora
from all_sky_alert.aurora import AuroraDetector, cell_geometry, opportunity_band
from all_sky_alert.domain import Conditions, DetectionContext, Observer, iso
from all_sky_alert.policy import alert_stages, qualified
from all_sky_alert.source_health import FreshnessPolicy, SourceHealth
from all_sky_alert.storage import Store
from all_sky_alert.swpc import SWPC
from all_sky_alert.service import Service
from all_sky_alert.web import create_app
from conftest import FakeConditions, RecordingAdapter


@pytest.fixture
def ovation(now):
    data = json.loads((Path(__file__).parent/"fixtures/ovation-sample.json").read_text())
    sparse = {(lon,lat):value for lon,lat,value in data["coordinates"]}
    data["coordinates"] = [[lon,lat,sparse.get((lon,lat),0)] for lon in range(360) for lat in range(-90,91)]
    data["Observation Time"] = iso(now)
    data["Forecast Time"] = iso(now+timedelta(minutes=15))
    return data


def provider(store, data, handler=None):
    transport = httpx.MockTransport(handler or (lambda r:httpx.Response(200,json=data)))
    return SWPC(httpx.Client(transport=transport),store,SWPC.defaults,["aurora"])


@pytest.mark.parametrize("mutation", [
    lambda d:d["coordinates"].pop(),
    lambda d:d["coordinates"].__setitem__(1,d["coordinates"][0]),
    lambda d:d["coordinates"][0].__setitem__(2,None),
    lambda d:d["coordinates"][0].__setitem__(2,101),
    lambda d:d["coordinates"][0].__setitem__(0,.5),
    lambda d:d.__setitem__("Forecast Time","2026-10-06T22:00:00Z"),
    lambda d:d.__setitem__("Observation Time","2026-10-06T18:00:00"),
])
def test_ovation_rejects_malformed_grid_and_times(ovation, mutation):
    mutation(ovation)
    with pytest.raises((ValueError,TypeError)):
        SWPC.validate(ovation)


def test_cache_restart_backoff_and_safe_error(store, ovation, now):
    calls = []
    def handler(request):
        calls.append(request.url)
        if len(calls)>1:
            return httpx.Response(429,headers={"Retry-After":"900"},text="do not log this remote body")
        return httpx.Response(200,json=ovation)
    source = provider(store,ovation,handler)
    try:
        source.refresh(now)
        assert source.snapshot(now)[1]["status"] == "healthy"
        assert len(calls)==1
        restart = SWPC(source.client,Store(store.path),SWPC.defaults,["aurora","second-consumer"])
        restart.refresh(now+timedelta(seconds=30))
        assert len(calls)==1 and restart.health.cached
        restart.refresh(now+timedelta(minutes=5))
        assert len(calls)==2 and restart.health.view(now+timedelta(minutes=5))["status"] == "degraded"
        restart.refresh(now+timedelta(minutes=6))
        assert len(calls)==2
        assert "HTTP 429" in restart.health.error and "remote body" not in restart.health.error
        assert len(store.source_health_rows())==1
        assert store.source_health_rows()[0]["consumers"]==["aurora","second-consumer"]
    finally:
        source.client.close()


def test_concurrent_shared_refresh_fetches_once(store, ovation, now):
    entered, release = Event(), Event()
    calls = []
    def handler(request):
        calls.append(request.url)
        entered.set()
        assert release.wait(5)
        return httpx.Response(200,json=ovation)
    source = provider(store,ovation,handler)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(source.refresh,now)
        try:
            assert entered.wait(3)
            second = pool.submit(source.refresh,now)
            release.set()
            first.result(timeout=5)
            second.result(timeout=5)
            assert len(calls)==1
            assert source.snapshot(now)[1]["status"]=="healthy"
        finally:
            release.set()
            source.client.close()


def test_http_200_does_not_make_old_model_fresh(store, ovation, now):
    # Forecast still temporally relevant, underlying solar-wind observation old.
    ovation["Observation Time"] = iso(now-timedelta(minutes=50))
    ovation["Forecast Time"] = iso(now+timedelta(minutes=15))
    source = provider(store,ovation)
    try:
        source.refresh(now)
        data, health = source.snapshot(now)
        assert health["status"] == "stale" and health["retrieved_age_seconds"] == 0
        assert health["age_seconds"] == 3000
        with pytest.raises(RuntimeError):
            source.snapshot(now+timedelta(minutes=11))
    finally:
        source.client.close()


@pytest.mark.parametrize("observed_offset,forecast_offset", [(1,15),(-90,-15),(-40,-30)])
def test_future_observation_old_data_or_expired_window_invalid(store, ovation, now, observed_offset, forecast_offset):
    ovation["Observation Time"] = iso(now+timedelta(minutes=observed_offset))
    ovation["Forecast Time"] = iso(now+timedelta(minutes=forecast_offset))
    source = provider(store,ovation)
    try:
        source.refresh(now)
        with pytest.raises(RuntimeError):
            source.snapshot(now)
    finally:
        source.client.close()


def test_disabled_and_bounded_response(store, ovation, now):
    source = provider(store,ovation,lambda r:httpx.Response(200,content=b" "*2_000_001))
    try:
        source.refresh(now)
        assert not source.health.available and source.health.error == "ValueError"
        source.settings = {**SWPC.defaults,"enabled":False}
        source.refresh(now)
        assert source.health.view(now)["status"]=="disabled"
        with pytest.raises(RuntimeError):
            source.snapshot(now)
    finally:
        source.client.close()


def test_horizon_geometry_and_hemispheres():
    observer = Observer(60,0,0,"UTC")
    high,bearing = cell_geometry(observer,60,0,110)
    assert high == pytest.approx(90)
    assert bearing is None
    low,north = cell_geometry(observer,65,0,110)
    upper,_ = cell_geometry(observer,65,0,250)
    assert 0<low<upper<30 and north==pytest.approx(0)
    assert cell_geometry(observer,60,180,250)[0]<0
    assert cell_geometry(Observer(-60,0,0,"UTC"),-65,0,250)[1]==pytest.approx(180)
    assert cell_geometry(Observer(0,0,0,"UTC"),0,90,250)[1]==pytest.approx(90)
    assert opportunity_band(4,90,"north")=="Photographic/marginal near horizon"
    assert "low north" in opportunity_band(10,40,"north")
    assert "Strong north" in opportunity_band(30,80,"north")
    assert "overhead" in opportunity_band(80,90,"south")
    assert "horizon" not in opportunity_band(80,20,"south")


class Sky:
    version = "fixture"
    sun_altitude = -18
    moon_altitude = -5
    def position(self, body, *args):
        return {"altitude_deg":self.sun_altitude if body=="Sun" else self.moon_altitude,"azimuth_deg":0}
    def moon_illumination(self, *args):
        return 1


def detect(source, now, latitude=60, sky=None):
    return AuroraDetector(AuroraDetector.defaults,source).detect(
        DetectionContext(Observer(latitude,0,0,"UTC"),now,2,sky or Sky()))


def test_aurora_quality_weather_moon_and_staleness(store, ovation, now, conditions, config):
    source = provider(store,ovation)
    try:
        source.refresh(now)
        event, = detect(source,now).candidates
        assert event.temporal_kind=="transient" and event.geometry["altitude_deg"] == 90
        assert "overhead" in event.title
        clear = aurora(event,conditions)
        cloudy = replace(conditions,metrics={**conditions.metrics,"cloud_total":replace(conditions.metrics["cloud_total"],value=100)})
        assert clear.quality>70 and aurora(event,cloudy).quality==0
        assert event.interest<config.policy(event.kind)["exceptional_interest"]
        assert not qualified(event,aurora(event,cloudy),config.policy(event.kind),now)
        moon_sky = Sky()
        moon_sky.moon_altitude = 30
        moon_event, = detect(source,now,sky=moon_sky).candidates
        assert aurora(moon_event,conditions).quality < clear.quality
        stale_event = replace(event,metadata={**event.metadata,"source_status":"stale"})
        assert not qualified(stale_event,aurora(stale_event,conditions),config.policy(event.kind),now)
        assert aurora(event,Conditions({},"unavailable",None,None)).quality is None
        assert alert_stages(event,clear,now,config.policy(event.kind))==["active"]
        assert not qualified(event,clear,config.policy(event.kind),now+timedelta(minutes=31))
    finally:
        source.client.close()


def test_daylight_and_oval_below_horizon_do_not_emit(store, ovation, now):
    source = provider(store,ovation)
    try:
        source.refresh(now)
        sky = Sky()
        sky.sun_altitude = 20
        assert detect(source,now,sky=sky).candidates == []
        assert detect(source,now,latitude=0).candidates == []
        southern, = detect(source,now,latitude=-60).candidates
        assert southern.geometry["altitude_deg"]==90
    finally:
        source.client.close()


def test_stable_night_identity_and_observation_provenance(store, ovation, now):
    source = provider(store,ovation)
    try:
        source.refresh(now)
        first, = detect(source,now).candidates
        updated = {**ovation,"Observation Time":iso(now+timedelta(minutes=5)),"Forecast Time":iso(now+timedelta(minutes=20))}
        source.client.close()
        source.client = httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=updated)))
        source.refresh(now+timedelta(minutes=5))
        second, = detect(source,now+timedelta(minutes=5)).candidates
        assert first.id == second.id and first.peak != second.peak
        assert second.metadata["observation_time"]==iso(now+timedelta(minutes=5))
        assert second.sources[0].name=="NOAA SWPC OVATION"
    finally:
        source.client.close()


def test_source_health_local_and_catalog_have_no_fabricated_observation_age(now):
    policy = FreshnessPolicy(86400,None,None)
    local = SourceHealth("ephemeris","local",iso(now),policy,["eclipses"],details={"version":"2.1.19"})
    assert local.view(now+timedelta(days=500))["status"]=="healthy"
    catalog = SourceHealth("catalog","catalog",iso(now),policy,[],retrieved_at=iso(now))
    assert catalog.view(now)["age_seconds"] is None
    assert catalog.view(now)["retrieved_age_seconds"]==0


@pytest.mark.parametrize("policy", [(0,None,None),(float("nan"),None,None),(300,60,30),(300,60,None),(300,None,-1)])
def test_invalid_source_policies_rejected(policy):
    with pytest.raises(ValueError):
        FreshnessPolicy(*policy)


def test_aurora_composed_ui_source_expiry_and_disabled_provider(config, store, conditions, ovation, now):
    config = replace(config,observer=Observer(60,0,0,"UTC"))
    clock = [now]
    source = provider(store,ovation)
    adapter = RecordingAdapter()
    detector = AuroraDetector(AuroraDetector.defaults,source)
    service = Service(config,store=store,conditions=FakeConditions(conditions),detectors=[detector],
                      astronomy=Sky(),sources={"swpc":source},adapters=[adapter],clock=lambda:clock[0])
    try:
        service.tick(now,lane="live",force=False,deliver=False)
        prediction, = service.upcoming()
        assert len(store.detail(prediction["candidate"]["id"],config.observer.key)["alerts"])==1
        with TestClient(create_app(service,background=False),base_url="http://localhost") as client:
            home = client.get("/")
            assert "<h2>Now</h2>" in home.text and "swpc.ovation" in home.text
            assert client.get("/api/predictions").json()["active"]
            detail = client.get("/events/"+prediction["candidate"]["id"])
            assert "Aurora horizon opportunity" in detail.text and "height scenarios" in detail.text
            assert "not local visual probability" in detail.text
            clock[0] = now+timedelta(minutes=31)
            assert "prediction inputs have expired" in client.get("/events/"+prediction["candidate"]["id"]).text
        service.dispatch(now+timedelta(minutes=31))
        assert not adapter.alerts
        assert store.detail(prediction["candidate"]["id"],config.observer.key)["deliveries"][0]["status"]=="CANCELED"
        # Disabling the source must not reuse a fresh detector batch in the live lane.
        source.settings = {**source.settings,"enabled":False}
        service.tick(now+timedelta(minutes=32),lane="live",force=False,deliver=False)
        assert next(h for h in store.health_rows() if h["name"]=="detector:aurora")["status"]=="error"
        assert store.detail(prediction["candidate"]["id"],config.observer.key)["state"]!="WITHDRAWN"
    finally:
        source.client.close()
        service.close()
