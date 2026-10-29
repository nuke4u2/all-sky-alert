from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from all_sky_alert.adsb import ADSBLol,normalize_readsb
from all_sky_alert.aircraft import AircraftDetector
from all_sky_alert.enrichment import LocalEnrichment
from all_sky_alert.domain import DetectionBatch,iso
from all_sky_alert.health import aggregate
from all_sky_alert.source_health import SourceHealth,FreshnessPolicy
from all_sky_alert.service import Service
from all_sky_alert.tracks import TrackBuffer
from all_sky_alert.web import create_app
from conftest import FakeConditions,RecordingAdapter


def view(name,now,status='healthy',kind='observation',consumers=None):
    return {'name':name,'status':status,'kind':kind,'consumers':consumers or ['adsb']}


def test_aggregate_required_optional_disabled_and_initialization(config,now):
    assert aggregate(config,['adsb'],[],[],now)['status']=='gray'
    healthy=view('adsb.lol',now)
    assert aggregate(config,['adsb'],[healthy],[],now)['status']=='green'
    config.values['providers']['enrichment']['enabled']=True
    optional=view('aircraft.enrichment',now,'unavailable',kind='catalog')
    assert aggregate(config,['adsb'],[healthy,optional],[],now)['status']=='yellow'
    config.values['providers']['enrichment']['enabled']=False
    assert aggregate(config,['adsb'],[healthy,optional],[],now)['status']=='green'
    assert aggregate(config,['adsb'],[view('adsb.lol',now,'stale')],[],now)['status']=='yellow'
    assert aggregate(config,['adsb'],[view('adsb.lol',now,'invalid')],[],now)['status']=='red'
    ephem=view('local.ephemeris',now,kind='local',consumers=['planetary'])
    data=[ephem,view('adsb.lol',now,'unavailable')]
    assert aggregate(config,['planetary','adsb'],data,[],now)['status']=='red'
    assert ephem['status']=='healthy'
    failed={'name':'worker:slow','status':'error','checked_at':iso(now),'message':'safe'}
    assert aggregate(config,['adsb'],[healthy],[failed],now)['status']=='red'
    stopped={**failed,'status':'ok','checked_at':iso(now-timedelta(hours=1))}
    assert aggregate(config,['adsb'],[healthy],[stopped],now)['status']=='red'


def test_provider_specific_age_not_one_global_ttl(config,now):
    config.values['atmosphere']['enabled']=False
    orbital=SourceHealth('orbits.25544','orbit',iso(now),FreshnessPolicy(7200,48*3600,96*3600),['iss'],retrieved_at=iso(now),data_at=iso(now-timedelta(hours=20))).view(now)
    live=SourceHealth('adsb.lol','observation',iso(now),FreshnessPolicy(15,30,90),['adsb'],retrieved_at=iso(now),data_at=iso(now-timedelta(seconds=40))).view(now)
    assert orbital['status']=='healthy' and live['status']=='stale'
    assert aggregate(config,['iss','adsb'],[orbital,live],[],now)['status']=='yellow'


def test_shared_conditions_missing_fallback_and_disabled_not_falsely_green(config,now):
    ephem=view('local.ephemeris',now,kind='local',consumers=['planetary'])
    assert aggregate(config,['planetary'],[ephem],[],now)['status']=='yellow'
    weather=view('open-meteo',now,kind='forecast',consumers=['planetary'])
    assert aggregate(config,['planetary'],[ephem,weather],[],now)['status']=='green'
    config.values['atmosphere']['canadian']['enabled']=True
    assert aggregate(config,['planetary'],[ephem,weather],[],now)['status']=='yellow'
    config.values['atmosphere']['enabled']=False
    stale=view('open-meteo',now,'unavailable',kind='forecast',consumers=['planetary'])
    assert aggregate(config,['planetary'],[ephem,stale],[],now)['status']=='green'


def test_required_source_disabled_with_consumer_still_enabled_is_unavailable(config,now):
    config.values['providers']['adsb']['enabled']=False
    assert aggregate(config,['adsb'],[],[],now)['status']=='red'
    config.values['providers']['adsb']['enabled']=True
    assert aggregate(config,['adsb'],[view('adsb.lol',now,'disabled')],[],now)['status']=='red'
    assert aggregate(config,[],[view('adsb.lol',now,'disabled')],[],now)['status']=='green'


def test_email_category_no_replay_and_pending_routing(config,store,conditions,event,now):
    class Detector:
        name='planetary'
        def detect(self,context):return DetectionBatch([event],{event.scope})
    adapter=RecordingAdapter();adapter.name='gmail'
    config.values['gmail']['enabled']=True
    config.values['gmail']['immediate'].update(event_types=['adsb'],minimum_interest=0)
    service=Service(config,store=store,conditions=FakeConditions(conditions),detectors=[Detector()],sources={},adapters=[adapter])
    try:
        service.tick(now)
        detail=store.detail(event.id,config.observer.key)
        assert detail['alerts'] and not detail['deliveries'] and not adapter.alerts
        config.values['gmail']['immediate']['event_types']=['planetary']
        service.tick(now+timedelta(seconds=10))
        assert not adapter.alerts # Existing ready milestone cannot replay.
        service.tick(event.start-timedelta(minutes=30),deliver=False)
        assert len(store.detail(event.id,config.observer.key)['deliveries'])==1
        config.values['gmail']['immediate']['event_types']=[]
        service.dispatch(event.start-timedelta(minutes=29))
        assert not adapter.alerts
        assert store.detail(event.id,config.observer.key)['deliveries'][0]['status']=='CANCELED'
    finally:service.close()


def test_map_routine_tracks_no_acquisition_on_reads_escaping_and_expiry(config,store,now,conditions):
    payload=json.loads(Path('tests/fixtures/adsb-radius.json').read_text());payload['ac']=payload['ac'][:1]
    payload['ac'][0]['flight']='<img src=x onerror=evil>'
    calls=[]
    client=httpx.Client(transport=httpx.MockTransport(lambda req:(calls.append(req) or httpx.Response(200,json=payload))))
    source=ADSBLol(client,store,config.observer,deepcopy(ADSBLol.defaults),['adsb'])
    enrichment=LocalEnrichment(client,store,config.observer,deepcopy(LocalEnrichment.defaults),['adsb'])
    detector=AircraftDetector(AircraftDetector.defaults,source,enrichment)
    clock=[now]
    service=Service(config,store=store,conditions=FakeConditions(conditions),detectors=[detector],sources={'adsb':source,'enrichment':enrichment},adapters=[],clock=lambda:clock[0])
    try:
        service.tick(now,lane='live')
        with TestClient(create_app(service,background=False),base_url='http://localhost') as browser:
            for _ in range(5):
                data=browser.get('/api/aircraft').json()
                assert data['aircraft'][0]['callsign']=='<img src=x onerror=evil>'
                assert data['aircraft'][0]['history']
                assert 'token' not in json.dumps(data).lower()
            assert len(calls)==1
            assert browser.get('/aircraft').status_code==200
            assert 'strict-origin-when-cross-origin'==browser.get('/aircraft').headers['Referrer-Policy']
            clock[0]=now+timedelta(seconds=31)
            assert browser.get('/api/aircraft').json()['aircraft']==[]
    finally:service.close();client.close()


def test_track_bounds_duplicate_old_and_retention(now):
    state=normalize_readsb(json.loads(Path('tests/fixtures/adsb-radius.json').read_text()))[0][0]
    buffer=TrackBuffer({'track_minutes':1,'max_aircraft':2,'max_points':3})
    for i in range(10):
        at=now+timedelta(seconds=i)
        buffer.update([replace(state,icao24=f'{j:06x}',position_at=iso(at),latitude=state.latitude+i*.001) for j in range(4)],at,30)
    assert len(buffer.rows)==2
    assert all(len(row['points'])==3 for row in buffer.rows.values())
    buffer.update([replace(state,icao24='000000',position_at=iso(now))],now+timedelta(seconds=10),30)
    assert len(buffer.rows['000000']['points'])==3
    assert not buffer.snapshot(now+timedelta(seconds=80),30)
    buffer.update([],now+timedelta(seconds=80),30);assert not buffer.rows


def test_filters_multiselect_none_and_database_health_failure(config,store,event,conditions,now,monkeypatch):
    class Detector:
        name='planetary'
        def detect(self,context):return DetectionBatch([event],{event.scope})
    service=Service(config,store=store,conditions=FakeConditions(conditions),detectors=[Detector()],sources={},adapters=[],clock=lambda:now)
    try:
        service.tick(now)
        with TestClient(create_app(service,background=False),base_url='http://localhost') as browser:
            assert browser.get('/api/predictions?filter=1&types=planetary&types=iss').json()['upcoming']
            assert not browser.get('/api/predictions?filter=1').json()['upcoming']
            assert not browser.get('/api/predictions?filter=1&types=adsb').json()['upcoming']
            assert browser.get('/?filter=1&types=unknown').status_code==422
            page=browser.get('/?filter=1&types=planetary').text
            assert 'width=device-width' in page and 'id="overall-health"' in page
            assert store.detail(event.id,config.observer.key) # Filtering changes no storage.
            def failure():raise OSError('unsafe database message')
            monkeypatch.setattr(store,'health_rows',failure)
            response=browser.get('/api/health')
            assert response.status_code==503 and response.json()['overall']['status']=='red'
            assert 'unsafe' not in response.text
    finally:service.close()
