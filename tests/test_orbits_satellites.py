from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from dataclasses import replace
import json

import httpx
import pytest

from all_sky_alert.domain import Observer,DetectionContext,iso,parse
from all_sky_alert.orbit_data import CelesTrak,OrbitRecord,normalize_omm
from all_sky_alert.orbits import OrbitGeometry,disk_contacts,root
from all_sky_alert.satellites import SatellitePassDetector,ISSTransitDetector
from all_sky_alert.assessment import satellite,transit
from all_sky_alert.policy import qualified
from all_sky_alert.config import DEFAULTS
from orbit_reference import model,position,separation,contacts,sunlit


FIXTURES = Path(__file__).parent/"fixtures"


@pytest.fixture
def omm_rows():
    return json.loads((FIXTURES/"iss-omm.json").read_text())


@pytest.fixture
def orbit(omm_rows):
    row=normalize_omm(omm_rows[0])
    epoch=iso(parse(row["EPOCH"]+"Z"))
    return OrbitRecord(25544,row["OBJECT_NAME"],epoch,row,"https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=JSON",epoch)


def provider(store,rows,now,handler=None):
    client=httpx.Client(transport=httpx.MockTransport(handler or (lambda request:httpx.Response(200,json=rows))))
    source=CelesTrak(client,store,None,deepcopy(CelesTrak.defaults),["iss","iss_transits"])
    source.refresh(now)
    return source,client


def test_omm_sgp4_against_vallado_vectors():
    data=json.loads((FIXTURES/"vallado-omm.json").read_text())
    row=normalize_omm(data["record"])
    record=OrbitRecord(5,row["OBJECT_NAME"],iso(parse(row["EPOCH"]+"Z")),row,"https://celestrak.org",iso(parse(row["EPOCH"]+"Z")))
    g=OrbitGeometry(record,Observer(0,0,0,"UTC"))
    for ref in data["reference"]["vectors"]:
        error,r,v=g.satellite.model.sgp4_tsince(ref["minutes"])
        assert error==0
        assert r==pytest.approx(ref["position"],abs=1e-5)
        assert v==pytest.approx(ref["velocity"],abs=1e-8)


@pytest.mark.parametrize("site",[(51.4779,0,46),(-33.8,151.2,100),(70,-179.8,0)])
def test_observer_geometry_independent_teme_gmst(orbit,site):
    observer=Observer(*site,"UTC")
    g=OrbitGeometry(orbit,observer)
    sat=model(orbit.elements)
    for minute in (0,45,125):
        t=parse(orbit.epoch)+timedelta(minutes=minute)
        actual=g.position(t)
        ref=position(sat,t,*site)
        assert actual["altitude_deg"]==pytest.approx(ref["altitude_deg"],abs=.02)
        assert actual["azimuth_deg"]==pytest.approx(ref["azimuth_deg"],abs=.02)
        assert actual["range_km"]==pytest.approx(ref["range_km"],abs=1)
        assert -10 <= actual["range_rate_km_s"] <= 10


def test_shadow_and_rise_max_set(orbit):
    g=OrbitGeometry(orbit,Observer(51.4779,0,46,"UTC"))
    epoch=parse(orbit.epoch)
    assert g.position(epoch)["sunlit"] is True
    assert g.position(epoch+timedelta(minutes=10))["sunlit"] is False
    for minute in (0,10,40,95):
        when=epoch+timedelta(minutes=minute)
        assert g.position(when)["sunlit"]==sunlit(model(orbit.elements),when)
    passes=g.passes(epoch,epoch+timedelta(hours=24))
    assert len(passes)==6
    rise,peak,end=passes[0]
    assert rise < peak < end
    assert abs(g.altitude(rise)) < .02
    assert abs(g.altitude(end)) < .02
    assert g.altitude(peak) > 8
    sat=model(orbit.elements)
    # Independent altitude root brackets the rise found by Skyfield.
    expected=root(lambda sec:position(sat,rise+timedelta(seconds=sec),51.4779,0,46)["altitude_deg"],-2,2)
    assert abs(expected)<.15


@pytest.mark.parametrize("case",json.loads((FIXTURES/"iss-transit-reference.json").read_text()))
def test_physical_subsecond_transits_independent(orbit,case,store,omm_rows):
    observer=Observer(**case["observer"])
    center=parse(case["center"])
    g=OrbitGeometry(orbit,observer)
    start=center-timedelta(seconds=20)
    def sample(s):
        v=g.apparent(start+timedelta(seconds=s),case["body"])
        return v["separation_deg"],v["radius_deg"]
    result=disk_contacts(sample,40)
    assert len(result)==1
    ingress,mid,egress=[start+timedelta(seconds=s) for s in result[0]]
    assert abs((ingress-parse(case["ingress"])).total_seconds()) < .1
    assert abs((egress-parse(case["egress"])).total_seconds()) < .1
    # Frozen references independently reproducible without product geometry/solver.
    independent=contacts(model(orbit.elements),center,case["observer"],case["body"])
    assert independent[0]==parse(case["ingress"])
    assert independent[1]==parse(case["egress"])
    ref_sep,_=separation(model(orbit.elements),mid,case["observer"],case["body"])
    assert abs(sample(result[0][1])[0]-ref_sep)<.02
    source,client=provider(store,omm_rows,center-timedelta(minutes=5))
    try:
        detector=ISSTransitDetector({**ISSTransitDetector.defaults,"bodies":[case["body"]],"horizon_hours":1},source)
        batch=detector.detect(DetectionContext(observer,center-timedelta(minutes=5),1,None))
        assert len(batch.candidates)==1
        event=batch.candidates[0]
        assert event.start < event.peak < event.end
        assert abs((event.start-ingress).total_seconds())<.002
        assert event.metadata["experimental"]
        assert event.metadata["orbit"]["uncertainty"]["timing_error_seconds"] is None
        assert g.position(center)["sun_altitude_deg"]>0 # Lunar daylight existence too.
        if case["body"]=="Sun":
            assert "front" in event.metadata["safety"]["text"]
        else:
            assert "safety" not in event.metadata
            assert event.metadata["duration_seconds"]<1
        shifted=OrbitGeometry(orbit,replace(observer,latitude=observer.latitude+1))
        def miss(s):
            v=shifted.apparent(start+timedelta(seconds=s),case["body"])
            return v["separation_deg"],v["radius_deg"]
        assert not disk_contacts(miss,40)
    finally:
        client.close()


@pytest.mark.parametrize("offset,expected",[(0,1),(.199,1),(.2,0),(.3,0)])
def test_disk_minimum_finds_between_coarse_samples(offset,expected):
    result=disk_contacts(lambda s:((((s-7.13)*2)**2+offset**2)**.5,.2),20)
    assert len(result)==expected
    if result:
        a,m,b=result[0]
        assert abs(m-7.13)<.001
        duration=(.2**2-offset**2)**.5
        assert abs(b-a-duration)<.002


def test_invalid_orbit_fields_and_future_epoch(omm_rows,store):
    for field,value in (("REF_FRAME","GCRF"),("TIME_SYSTEM","TAI"),("MEAN_MOTION",float("nan")),("NORAD_CAT_ID",True)):
        with pytest.raises((ValueError,TypeError)):
            normalize_omm({**omm_rows[0],field:value})
    epoch=parse(omm_rows[0]["EPOCH"]+"Z")
    source,client=provider(store,omm_rows,epoch-timedelta(minutes=2))
    assert not source.records(epoch-timedelta(minutes=2))
    client.close()


def test_orbit_shared_cache_age_restart_and_http_stop(store,omm_rows):
    epoch=parse(omm_rows[0]["EPOCH"]+"Z")
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json=omm_rows)
    source,client=provider(store,omm_rows,epoch,handler)
    source.refresh(epoch+timedelta(minutes=15))
    assert len(calls)==1
    other=CelesTrak(client,store,None,deepcopy(CelesTrak.defaults),["iss"])
    other.refresh(epoch+timedelta(minutes=30))
    assert len(calls)==1
    assert other.health.view(epoch+timedelta(hours=25))["status"]=="warning"
    assert other.health.view(epoch+timedelta(hours=49))["status"]=="stale"
    assert other.health.view(epoch+timedelta(hours=97))["status"]=="invalid"
    assert not other.records(epoch+timedelta(hours=97))
    client.close()
    fails=[]
    client=httpx.Client(transport=httpx.MockTransport(lambda req:(fails.append(1) or httpx.Response(403,text="unsafe raw body"))))
    other=CelesTrak(client,store,None,deepcopy(CelesTrak.defaults),["iss"])
    other.refresh(epoch+timedelta(hours=2))
    other.refresh(epoch+timedelta(hours=4))
    assert len(fails)==1
    assert other.health.details["http_stopped"]
    assert "unsafe" not in other.health.error
    other.reset_stop()
    other.refresh(epoch+timedelta(hours=6))
    assert len(fails)==2
    client.close()


def test_transport_retry_deadline_does_not_slide_on_cached_evaluations(store,omm_rows):
    epoch=parse(omm_rows[0]["EPOCH"]+"Z")
    calls=[]
    def handler(request):
        calls.append(request)
        if len(calls)==1:
            raise httpx.ConnectError("offline",request=request)
        return httpx.Response(200,json=omm_rows)
    source,client=provider(store,omm_rows,epoch,handler)
    try:
        deadline=store.cache(source.key(25544))["next_attempt"]
        assert parse(deadline)==epoch+timedelta(hours=2)
        for minute in (5,30,60,119):
            source.refresh(epoch+timedelta(minutes=minute))
            assert store.cache(source.key(25544))["next_attempt"]==deadline
        assert len(calls)==1
        source.refresh(epoch+timedelta(hours=2))
        assert len(calls)==2 and source.records(epoch+timedelta(hours=2))
        assert source.health.error is None
    finally:
        client.close()


def test_passes_and_transit_epoch_gates_and_independent_weather(store,omm_rows,conditions):
    epoch=parse(omm_rows[0]["EPOCH"]+"Z")
    source,client=provider(store,omm_rows,epoch)
    observer=Observer(51.4779,0,46,"Europe/London")
    context=DetectionContext(observer,epoch,2,None)
    detector=SatellitePassDetector(SatellitePassDetector.defaults,source)
    try:
        events=detector.detect(context).candidates
        assert events
        for event in events:
            assert event.geometry["sunlit"]
            assert event.geometry["sun_altitude_deg"]<=-6
            assert event.geometry["altitude_deg"]>=20
            assert len(event.metadata["contacts"])==3
            clear=satellite(event,conditions)
            cloudy=satellite(event,replace(conditions,metrics={**conditions.metrics,"cloud_total":replace(conditions.metrics["cloud_total"],value=100)}))
            assert clear.quality > cloudy.quality
            assert event.interest < 90
        source.refresh(epoch+timedelta(hours=13))
        assert not ISSTransitDetector(ISSTransitDetector.defaults,source).detect(replace(context,now=epoch+timedelta(hours=13))).candidates
        source.refresh(epoch+timedelta(hours=49))
        old=detector.detect(replace(context,now=epoch+timedelta(hours=49))).candidates
        assert old
        assert all(not qualified(e,satellite(e,conditions),DEFAULTS["notifications"],epoch+timedelta(hours=49)) for e in old)
    finally:
        client.close()
