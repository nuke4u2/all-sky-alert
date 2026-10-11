from copy import deepcopy
from dataclasses import asdict,replace
from datetime import timedelta
from pathlib import Path
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from all_sky_alert.config import DEFAULTS
from all_sky_alert.domain import Assessment,Candidate,Conditions,DetectionBatch,DetectionContext,Observer,iso,parse
from all_sky_alert.orbit_data import CelesTrak,OrbitRecord,normalize_omm
from all_sky_alert.orbits import OrbitGeometry
from all_sky_alert.satellites import ISSTransitDetector,SatellitePassDetector
from all_sky_alert.adsb import ADSBLol
from all_sky_alert.aircraft import AircraftDetector
from all_sky_alert.enrichment import LocalEnrichment
from all_sky_alert.assessment import transit
from all_sky_alert.notifications import render_message
from all_sky_alert.service import Service
from all_sky_alert.storage import Store
from all_sky_alert.web import create_app
from conftest import FakeConditions,RecordingAdapter


def test_scheduled_milestones_do_not_wait_for_geometry_and_expired_inputs_do_not_send(config,store,event,conditions,now):
    class Slow:
        name="iss"
        calls=0
        def detect(self,context):
            self.calls+=1
            return DetectionBatch([self.event],{self.event.scope})
    class Orbits:
        def refresh(self,now):pass
    detector=Slow()
    detector.event=replace(event,detector="iss",kind="iss.pass",start=now+timedelta(minutes=10),
        peak=now+timedelta(minutes=12),end=now+timedelta(minutes=15),profile="satellite",valid_until=iso(now+timedelta(minutes=13)))
    policy=config.values["notifications"]
    policy.update(ready_lead_hours=1,imminent_lead_minutes=5)
    adapter=RecordingAdapter()
    service=Service(config,store=store,conditions=FakeConditions(conditions),detectors=[detector],sources={"orbits":Orbits()},adapters=[adapter])
    try:
        service.tick(now)
        assert detector.calls==1
        assert [a["stage"] for a in adapter.alerts]==["ready"]
        service.dispatch(now+timedelta(minutes=6))
        assert detector.calls==1
        assert [a["stage"] for a in adapter.alerts]==["ready","imminent"]
        service.dispatch(now+timedelta(minutes=7))
        assert len(adapter.alerts)==2
        restarted=Service(config,store=Store(store.path),conditions=FakeConditions(conditions),detectors=[detector],sources={"orbits":Orbits()},adapters=[adapter])
        restarted.dispatch(now+timedelta(minutes=8));restarted.close()
        assert len(adapter.alerts)==2
        # Clock-only milestones cannot overwrite a newer detector payload.
        old=store.detail(event.id,config.observer.key)
        new={k:v for k,v in old.items() if k not in {"alerts","deliveries","revisions"}}
        new["candidate"]={**new["candidate"],"title":"Latest orbital forecast"}
        store.save(new,now+timedelta(minutes=8,seconds=1),[],[])
        assert not store.save(old,now+timedelta(minutes=9),["watch"],[],expected_evaluated_at=old["evaluated_at"])
        assert store.detail(event.id,config.observer.key)["candidate"]["title"]=="Latest orbital forecast"
    finally:
        service.close()


def test_short_lane_independent_detector_source_cadences(config,store,event,conditions,now):
    class Source:
        def __init__(self):self.calls=0
        def refresh(self,now):self.calls+=1
    class Detector:
        def __init__(self,name,event):self.name,self.event,self.calls=name,event,0
        def detect(self,context):
            self.calls+=1
            return DetectionBatch([self.event],{self.event.scope})
    craft=replace(event,id="craft",detector="adsb",kind="aircraft.episode",profile="aircraft",temporal_kind="transient")
    aurora=replace(event,id="aur",detector="aurora",kind="aurora.opportunity",profile="aircraft",temporal_kind="transient")
    a,b=Detector("adsb",craft),Detector("aurora",aurora)
    sources={name:Source() for name in ("adsb","enrichment","swpc")}
    service=Service(config,store=store,conditions=FakeConditions(conditions),detectors=[a,b],sources=sources,adapters=[])
    try:
        assert service.lane_interval("live")==15
        for second in range(0,61,15):service.tick(now+timedelta(seconds=second),lane="live",force=False)
        assert a.calls==5 and b.calls==1
        assert sources["adsb"].calls==5 and sources["swpc"].calls==1
        assert service.conditions.refreshes==0
    finally:service.close()


def test_orbit_update_invalidates_cached_geometry(config,store,event,conditions,now):
    class Orbits:
        identity=1
        def refresh(self,now):pass
    class Detector:
        name="iss"
        calls=0
        @property
        def cache_identity(self):return source.identity
        def detect(self,context):
            self.calls+=1
            e=replace(event,detector="iss",profile="satellite")
            return DetectionBatch([e],{e.scope})
    source=Orbits();detector=Detector()
    service=Service(config,store=store,conditions=FakeConditions(conditions),detectors=[detector],sources={"orbits":source},adapters=[])
    try:
        service.tick(now,force=False)
        service.tick(now+timedelta(seconds=15),force=False)
        assert detector.calls==1
        source.identity=2
        service.tick(now+timedelta(seconds=30),force=False)
        assert detector.calls==2
    finally:service.close()


def test_revolution_identity_survives_epoch_advance():
    row=normalize_omm(json.loads(Path("tests/fixtures/iss-omm.json").read_text())[0])
    epoch=parse(row["EPOCH"]+"Z")
    observer=Observer(51.4779,0,46,"UTC")
    def geometry(elements):
        return OrbitGeometry(OrbitRecord(25544,"ISS",iso(parse(elements["EPOCH"]+"Z")),elements,"https://example.org",iso(epoch)),observer)
    original=geometry(row)
    # Same mean-orbit phase one revolution later with counter advanced.
    update={**row,"EPOCH":iso(epoch+timedelta(days=1/row["MEAN_MOTION"])).removesuffix("+00:00"),"REV_AT_EPOCH":row["REV_AT_EPOCH"]+1}
    revised=geometry(update)
    target=epoch+timedelta(hours=4)
    assert original.occurrence(target)==revised.occurrence(target)


def test_transit_details_email_safety_precision_and_stale_scientific_bypass(config,store,conditions):
    cases=json.loads(Path("tests/fixtures/iss-transit-reference.json").read_text())
    case=cases[0];center=parse(case["center"])
    config=replace(config,observer=Observer(**case["observer"]))
    rows=json.loads(Path("tests/fixtures/iss-omm.json").read_text())
    client=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=rows)))
    source=CelesTrak(client,store,config.observer,deepcopy(CelesTrak.defaults),["iss_transits"])
    detector=ISSTransitDetector({**ISSTransitDetector.defaults,"bodies":["Sun"],"horizon_hours":1},source)
    now=center-timedelta(minutes=5)
    current=[now]
    config.values["detectors"]["iss_transits"]["enabled"]=True
    service=Service(config,store=store,conditions=FakeConditions(conditions),detectors=[detector],sources={"orbits":source},adapters=[],clock=lambda:current[0])
    try:
        service.tick(now)
        rows=service.upcoming(now)
        assert len(rows)==1
        e=rows[0]["candidate"]
        with TestClient(create_app(service,background=False),base_url="http://localhost") as web:
            page=web.get("/events/"+e["id"])
            assert "Nominal ISS disk transit" in page.text
            assert "front" in page.text and "filter" in page.text
            assert "solver tolerance" in page.text
            assert e["start"][11:19] in page.text
            assert web.get("/api/events/"+e["id"]).json()["candidate"]["metadata"]["duration_seconds"]>0
        message=render_message({"id":"test","stage":"imminent","prediction":rows[0]},"user@example.org")
        body=message.get_content()
        assert "front" in body and "filter" in body
        assert "experimental" in body.lower()
        event=Candidate.from_dict(e)
        expired=replace(event,valid_until=iso(now-timedelta(seconds=1)))
        from all_sky_alert.policy import alert_stages
        assert alert_stages(expired,transit(expired,conditions),now,config.policy(event.kind))==[]
    finally:service.close();client.close()


@pytest.mark.parametrize("unexpected",[False,True])
def test_failed_enrichment_and_provider_gap_do_not_reset_episode(config,store,now,conditions,tmp_path,monkeypatch,unexpected):
    rows=json.loads(Path("tests/fixtures/adsb-radius.json").read_text());rows["ac"]=rows["ac"][:1]
    fail=[False]
    def handler(request):
        return httpx.Response(503) if fail[0] else httpx.Response(200,json=rows)
    client=httpx.Client(transport=httpx.MockTransport(handler))
    source=ADSBLol(client,store,config.observer,deepcopy(ADSBLol.defaults),["adsb"])
    enrichment=LocalEnrichment(client,store,config.observer,{**LocalEnrichment.defaults,"enabled":True,"path":str(tmp_path/"missing.json")},["adsb"])
    if unexpected:
        def broken(*args):raise RuntimeError("Optional source unavailable")
        monkeypatch.setattr(enrichment,"refresh",broken)
        monkeypatch.setattr(enrichment,"at",broken)
    detector=AircraftDetector(AircraftDetector.defaults,source,enrichment)
    service=Service(config,store=store,conditions=FakeConditions(conditions),detectors=[detector],sources={"adsb":source,"enrichment":enrichment},adapters=[])
    try:
        service.tick(now,lane="live")
        event=service.upcoming(now)[0]["candidate"]
        if not unexpected:
            assert enrichment.health.error=="FileNotFoundError"
        assert event["metadata"]["enrichment"] is None
        fail[0]=True
        service.tick(now+timedelta(seconds=15),lane="live")
        assert store.detail(event["id"],config.observer.key)["candidate"]["id"]==event["id"]
        service.tick(now+timedelta(seconds=95),lane="live")
        assert service.recent(now+timedelta(seconds=95))
        fail[0]=False;rows["now"]=(now+timedelta(seconds=150)).timestamp()*1000
        service.tick(now+timedelta(seconds=150),lane="live")
        restored=service.upcoming(now+timedelta(seconds=150))[0]["candidate"]
        assert restored["id"]==event["id"]
        assert len(store.detail(event["id"],config.observer.key)["alerts"])==1
    finally:service.close();client.close()


def test_aircraft_privacy_flags_skip_without_reconstruction():
    from all_sky_alert.adsb import normalize_readsb
    rows=json.loads(Path("tests/fixtures/adsb-radius.json").read_text())
    rows["ac"]=rows["ac"][:1]
    for flags in (4,8,12):
        rows["ac"][0]["dbFlags"]=flags
        assert normalize_readsb(rows)[0]==[]


def test_after_http_clock_used_for_live_evaluation(config,store,event,conditions,now):
    clock=[now]
    class Source:
        def refresh(self,now):clock[0]=now+timedelta(seconds=2)
    class Detector:
        name="adsb"
        times=[]
        def detect(self,context):
            self.times.append(context.now)
            return DetectionBatch([],{})
    detector=Detector()
    service=Service(config,store=store,conditions=FakeConditions(conditions),detectors=[detector],sources={"adsb":Source(),"enrichment":Source()},adapters=[],clock=lambda:clock[0])
    try:
        service.tick(lane="live")
        assert detector.times==[now+timedelta(seconds=2)]
    finally:service.close()
