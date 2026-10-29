from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from all_sky_alert.adsb import ADSBLol,AircraftState,normalize_readsb
from all_sky_alert.aircraft import AircraftDetector,motion
from all_sky_alert.enrichment import LocalEnrichment
from all_sky_alert.domain import Conditions,DetectionContext,Observer,iso,parse
from all_sky_alert.assessment import aircraft
from all_sky_alert.config import DEFAULTS,load
from all_sky_alert.policy import alert_stages,qualified
from all_sky_alert.service import Service
from all_sky_alert.storage import Store
from all_sky_alert.source_health import views
from all_sky_alert.web import create_app
from conftest import FakeConditions,RecordingAdapter


@pytest.fixture
def radius():
    return json.loads((Path(__file__).parent/"fixtures"/"adsb-radius.json").read_text())


def setup_sources(store,config,radius,now,handler=None,enrichment_path=None):
    client=httpx.Client(transport=httpx.MockTransport(handler or (lambda req:httpx.Response(200,json=radius))))
    source=ADSBLol(client,store,config.observer,deepcopy(ADSBLol.defaults),["adsb"])
    metadata=LocalEnrichment(client,store,config.observer,{**LocalEnrichment.defaults,
        "enabled":enrichment_path is not None,"path":str(enrichment_path or "unused.json")},["adsb"])
    source.refresh(now);metadata.refresh(now)
    detector=AircraftDetector(deepcopy(AircraftDetector.defaults),source,metadata)
    return source,metadata,detector,client


def test_normalized_provider_contract(radius,now):
    states,feed=normalize_readsb(radius)
    assert feed==now
    assert len(states)==2
    assert states[0].callsign=="JBU123"
    assert states[0].altitude_m==pytest.approx(2499.36)
    assert states[0].speed_m_s==pytest.approx(92.59992)
    assert states[0].position_at==iso(now-timedelta(seconds=2))
    assert states[1].altitude_kind=="barometric approximation"
    assert states[1].position_kind=="mlat"


@pytest.mark.parametrize("key,value",[("seen_pos",-1),("lat",91),("lon",float("nan")),("gs",-1),("alt_geom",1e9),("flight","evil\nheader")])
def test_untrusted_position_fields_fail_closed(radius,key,value):
    radius["ac"][0][key]=value
    with pytest.raises(ValueError):
        normalize_readsb(radius)


def test_unknown_motion_and_observer_geometry(now):
    observer=Observer(0,0,0,"UTC")
    state=AircraftState("abc123",iso(now),0,0,3000,"geometric",100,90,0)
    result=motion(state,observer,180)
    assert result["altitude_deg"]==pytest.approx(90)
    assert result["range_km"]==pytest.approx(3)
    assert result["from_direction"]=="west"
    assert result["toward_direction"]=="east"
    assert result["closest_in_seconds"]==pytest.approx(0)
    distant=replace(state,longitude=-.1)
    projected=motion(distant,observer,180)
    assert 100<projected["closest_in_seconds"]<120
    assert projected["closest_range_km"]==pytest.approx(3,abs=.02)
    assert projected["closest_altitude_deg"]>89
    older=motion(distant,observer,180,10)
    assert older["closest_in_seconds"]==pytest.approx(projected["closest_in_seconds"]-10,abs=.1)
    assert older["range_km"]<projected["range_km"]
    far=motion(replace(state,longitude=-1),observer,180)
    assert far["closest_in_seconds"] is None
    unknown=motion(replace(state,speed_m_s=None,track_deg=None),observer,180)
    assert unknown["toward_direction"] is None
    assert unknown["closest_in_seconds"] is None


def test_episode_identity_gap_hysteresis_and_restart(store,config,now):
    a=store.track_episode(config.observer.key,"abc123",now,180,{"x":1})
    b=store.track_episode(config.observer.key,"abc123",now+timedelta(seconds=30),180,{"x":2})
    assert a["episode_id"]==b["episode_id"]
    restarted=Store(store.path)
    c=restarted.track_episode(config.observer.key,"abc123",now+timedelta(seconds=150),180,{"x":3})
    assert c["episode_id"]==a["episode_id"]
    # An out-of-order sample cannot extend continuity or erase latest state.
    stale=restarted.track_episode(config.observer.key,"abc123",now+timedelta(seconds=20),180,{"x":4})
    assert stale["last_position_at"]==c["last_position_at"]
    d=restarted.track_episode(config.observer.key,"abc123",now+timedelta(seconds=400),180,{"x":5})
    assert d["episode_id"]!=a["episode_id"]


def test_live_detection_stale_and_non_weather_policy(store,config,radius,now):
    source,meta,detector,client=setup_sources(store,config,radius,now)
    try:
        batch=detector.detect(DetectionContext(config.observer,now,1,None))
        assert batch.candidates
        event=batch.candidates[0]
        assert event.metadata["track_state"]=="OVERHEAD"
        assert event.metadata["route_status"]=="route unknown"
        assert "JBU123" in event.reasons[0]
        assert "JFK" not in event.reasons[0]
        assessment=aircraft(event,Conditions({},"disabled",None,None))
        assert qualified(event,assessment,DEFAULTS["notifications"],now)
        assert not qualified(event,assessment,DEFAULTS["notifications"],now+timedelta(seconds=31))
        assert not detector.detect(DetectionContext(config.observer,now+timedelta(seconds=31),1,None)).candidates
        assert source.health.view(now+timedelta(seconds=31))["status"]=="stale"
    finally:
        client.close()


def test_approaching_constant_motion_no_route_guess(store,config,radius,now):
    observer=Observer(0,0,0,"UTC")
    config=replace(config,observer=observer)
    radius["ac"]=[{"hex":"abc123","lat":0,"lon":-.1,"alt_geom":9842.52,"gs":194.3846,"track":90,"seen_pos":0}]
    source,meta,detector,client=setup_sources(store,config,radius,now)
    try:
        event=detector.detect(DetectionContext(observer,now,1,None)).candidates[0]
        assert event.metadata["track_state"]=="APPROACHING"
        assert event.start>now
        assert event.peak<=event.end
        assert event.valid_until < iso(event.start) # Requires updated input to survive approach.
        assert event.geometry["from_direction"]=="west"
        assert event.geometry["toward_direction"]=="west" # Still west in the next 30s, not an invented east sky location.
        assert "Aircraft ABC123" in event.title
        assert not event.metadata["enrichment"]
    finally:
        client.close()


def enrichment_record(now):
    return {"icao24":"abc123","callsign":"JBU123","type_name":"Airbus A320","origin":"JFK","destination":"BOS",
        "updated_at":iso(now-timedelta(hours=1)),"valid_until":iso(now+timedelta(hours=3)),
        "source_name":"Test user","source_url":"https://example.org/","rights":"Synthetic test fixture; user-owned"}


def test_route_enrichment_success_age_unknown_failure_non_gating(store,config,radius,now,tmp_path):
    path=tmp_path/"enrichment.json"
    path.write_text(json.dumps([enrichment_record(now)]))
    source,meta,detector,client=setup_sources(store,config,radius,now,enrichment_path=path)
    try:
        event=detector.detect(DetectionContext(config.observer,now,1,None)).candidates[0]
        assert "from JFK to BOS" in event.metadata["prose"]
        assert "Airbus A320" in event.metadata["prose"]
        assert event.metadata["enrichment"]["age_seconds"]==3600
        assert meta.at(replace(source.snapshot(now)[0][0],callsign="JBU124"),now) is None
        assert meta.at(source.snapshot(now)[0][0],now+timedelta(hours=4)) is None
        path.write_text("broken file")
        later=now+timedelta(hours=25)
        meta.refresh(later)
        assert meta.health.error
        assert meta.at(source.snapshot(now)[0][0],later) is None
        # Enrichment error does not take the positional detector down.
        event=detector.detect(DetectionContext(config.observer,now,1,None)).candidates[0]
        assert event.metadata["route_status"]=="route unknown"
    finally:
        client.close()


def test_enrichment_requires_bound_route_and_rights(now):
    row=enrichment_record(now)
    for key in ("rights","icao24","callsign","destination","source_url"):
        broken={**row};broken.pop(key)
        with pytest.raises((ValueError,KeyError)):
            LocalEnrichment.validate([broken])


def test_interest_rules_and_emergency_are_not_route_inference(store,config,radius,now):
    radius["ac"][0]["squawk"]="7700"
    source,meta,detector,client=setup_sources(store,config,radius,now)
    try:
        detector.settings["interesting_types"]=["A320"]
        event=detector.detect(DetectionContext(config.observer,now,1,None)).candidates[0]
        assert event.interest==85
        assert any("configured" in reason for reason in event.reasons)
        assert any("7700" in reason and "unverified" in reason for reason in event.reasons)
    finally:
        client.close()


def test_cache_429_backoff_outage_and_position_health(store,config,radius,now):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json=radius) if len(calls)==1 else httpx.Response(429,headers={"Retry-After":"300"},text="unsafe raw data")
    source,meta,detector,client=setup_sources(store,config,radius,now,handler)
    try:
        source.refresh(now+timedelta(seconds=5))
        assert len(calls)==1
        source.refresh(now+timedelta(seconds=15))
        assert len(calls)==2
        assert source.health.error=="HTTPStatusError HTTP 429"
        assert source.health.next_refresh==iso(now+timedelta(seconds=315))
        source.refresh(now+timedelta(seconds=30))
        assert len(calls)==2
        assert source.health.view(now+timedelta(seconds=30))["position_fresh_count"]==0
        assert source.health.view(now+timedelta(seconds=30))["status"]=="stale"
        with pytest.raises(RuntimeError):
            source.snapshot(now+timedelta(seconds=100))
        assert source.health.view(now+timedelta(seconds=100))["status"]=="invalid"
    finally:
        client.close()


def test_successful_http_does_not_freshen_old_positions(store,config,radius,now):
    radius["ac"]=radius["ac"][:1]
    radius["ac"][0]["seen_pos"]=120
    source,meta,detector,client=setup_sources(store,config,radius,now)
    try:
        assert source.health.view(now)["status"]=="invalid"
        with pytest.raises(RuntimeError):
            detector.detect(DetectionContext(config.observer,now,1,None))
    finally:
        client.close()


def test_episode_alert_revision_restart_and_live_ui_expiry(store,config,radius,now,conditions):
    radius["ac"]=radius["ac"][:1]
    radius["ac"][0]["flight"]="<script>"
    source,meta,detector,client=setup_sources(store,config,radius,now)
    adapter=RecordingAdapter()
    config.values["detectors"]["adsb"]["enabled"]=True
    current=[now]
    service=Service(config,store=store,conditions=FakeConditions(conditions),detectors=[detector],sources={"adsb":source,"enrichment":meta},adapters=[adapter],clock=lambda:current[0])
    try:
        for seconds in range(0,91,15):
            current[0]=now+timedelta(seconds=seconds)
            radius["now"]=current[0].timestamp()*1000
            radius["ac"][0]["lat"]+=.00001
            service.tick(current[0],lane="live",force=False)
        rows=service.upcoming(current[0])
        assert len(rows)==1
        event=rows[0]["candidate"]
        detail=store.detail(event["id"],config.observer.key)
        assert len(detail["revisions"])>1
        assert len(detail["alerts"])==1
        assert len(adapter.alerts)==1
        source2,meta2,detector2,client2=setup_sources(Store(store.path),config,radius,current[0])
        assert detector2.detect(DetectionContext(config.observer,current[0],1,None)).candidates[0].id==event["id"]
        client2.close()
        with TestClient(create_app(service,background=False),base_url="http://localhost") as web:
            home=web.get("/")
            assert "live.js" in home.text
            assert "script-src 'self'" in home.headers["Content-Security-Policy"]
            data=web.get("/api/live").json()
            assert len(data["aircraft"])==1
            assert data["aircraft"][0]["title"].startswith("<script>")
            page=web.get("/events/"+event["id"])
            assert "&lt;script&gt;" in page.text
            assert "<script>" not in page.text
            assert web.get("/static/live.js").status_code==200
            current[0]+=timedelta(seconds=35)
            assert web.get("/api/live").json()["aircraft"]==[]
            assert web.get("/api/predictions").json()["active"]==[]
            assert web.get("/api/events/"+event["id"]).status_code==200
    finally:
        service.close();client.close()


def test_additive_schema_two_episode_migration_retains_alerts(store,config,now):
    with store.connection() as db:
        db.execute("DROP TABLE track_episodes")
        db.execute("PRAGMA user_version=2")
        db.execute("INSERT INTO health VALUES ('kept','ok',?,'unchanged')",(iso(now),))
    restored=Store(store.path)
    assert any(row["name"]=="kept" for row in restored.health_rows())
    episode=restored.track_episode(config.observer.key,"abc123",now,180,{})
    assert episode["episode_id"]
    with restored.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0]==4


def test_phase3_defaults_validation_and_dst(tmp_path):
    example=Path("config.example.toml").read_text()
    path=tmp_path/"example.toml";path.write_text(example)
    config=load(path)
    assert config.values["detectors"]["adsb"]["enabled"]
    assert all(DEFAULTS["detectors"][name]["enabled"] is False for name in ("iss","iss_transits","adsb"))
    for before,after in (("cache_seconds = 7200","cache_seconds = 60"),("stale_seconds = 30","stale_seconds = 400"),
                         ("projection_seconds = 180","projection_seconds = 900"),("catalog_ids = [25544] # Include","catalog_ids = [1] # Include")):
        path.write_text(example.replace(before,after))
        with pytest.raises(ValueError):load(path)
    from zoneinfo import ZoneInfo
    zone=ZoneInfo("America/New_York")
    a=parse("2026-11-01T05:30:00Z");b=parse("2026-11-01T06:30:00Z")
    assert a.astimezone(zone).hour==b.astimezone(zone).hour==1
    assert a.astimezone(zone).utcoffset()!=b.astimezone(zone).utcoffset()
