from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import json

import httpx
import pytest

from all_sky_alert.config import Config,DEFAULTS
from all_sky_alert.crystals import PHENOMENA,SkyCrystalDetector,possibility,suitability
from all_sky_alert.domain import DetectionContext,Metric,Observer,iso,parse
from all_sky_alert.hrrr import FIELDS,PARAMETERS,HRRR,select_ranges,decode_point
from all_sky_alert.service import Service
from conftest import FakeConditions


@pytest.mark.parametrize('kind,altitude,illumination,expected',[
    ('cza',20,1,True),('cza',33,1,False),('cza',-1,1,False),
    ('upper_tangent',20,1,True),('upper_tangent',32,1,False),
    ('lower_tangent',10,1,False),('lower_tangent',26,1,True),
    ('pillar',5,1,True),('pillar',20,1,False),('lunar_halo',20,.7,True),('lunar_halo',20,.2,False)])
def test_geometry_screens(kind,altitude,illumination,expected):assert possibility(kind,altitude,illumination)==expected


def test_coarse_crystals_obstruction_diamond_dust_separate(conditions,now):
    s=SkyCrystalDetector.defaults
    clear=suitability('cza',conditions,None,s)
    blocked=replace(conditions,metrics={**conditions.metrics,'cloud_low':replace(conditions.metrics['cloud_low'],value=95)})
    assert clear['score']>suitability('cza',blocked,None,s)['score']
    assert clear['orientation']=='unknown' and clear['confidence']<.5
    cold=replace(blocked,metrics={**blocked.metrics,'temperature_surface':Metric(-25,'°C',.8,'fixture','fixture',iso(now)),
                              'humidity':replace(conditions.metrics['humidity'],value=90)})
    dust=suitability('diamond_dust',cold,None,s)
    assert dust and dust['foreground_obstruction_percent']==0
    assert 'inversion' in dust['method']
    warm=replace(cold,metrics={**cold.metrics,'temperature_surface':replace(cold.metrics['temperature_surface'],value=5)})
    assert suitability('diamond_dust',warm,None,s) is None


def profile(now):
    values={'TMP':240,'PRES':30000,'SPFH':.001,'HGT':8000,'CIMIXR':1e-5,'NCCICE':10000,'CLMR':0}
    units={field:unit for field,unit in PARAMETERS.values()}
    return {'issued_at':iso(now-timedelta(hours=3)),'retrieved_at':iso(now),'valid_at':iso(now),
            'valid_until':iso(now+timedelta(hours=1)),'source_url':'https://example.org/hrrr.grib2',
            'layers':[{'level':20,'fields':{field:{'value':value,'units':units[field],'distance_km':1} for field,value in values.items()}}]}


def test_microphysics_uses_units_cold_layers_and_keeps_orientation_unknown(conditions,now):
    good=profile(now)
    result=suitability('halo22',conditions,good,SkyCrystalDetector.defaults)
    assert result['layers'][0]['favorable'] and result['crystal_suitability']==75
    assert result['orientation']=='unknown' and result['confidence']==.45
    good['layers'][0]['fields']['CIMIXR']['units']='unknown'
    fallback=suitability('halo22',conditions,good,SkyCrystalDetector.defaults)
    assert not fallback['layers'] and 'Coarse' in fallback['method']


def test_shared_conditions_detector_updates_identity_and_provider_failure(config,store,conditions,now):
    class Astro:
        version='fixture'
        def position(self,body,when,observer):return {'altitude_deg':20,'geometric_altitude_deg':20,'azimuth_deg':150}
        def moon_illumination(self,when):return .75
    class Failing:
        def refresh(self,now):raise RuntimeError('untrusted upstream')
        def at(self,*args):raise RuntimeError('untrusted upstream')
    shared=FakeConditions(conditions)
    detector=SkyCrystalDetector({**SkyCrystalDetector.defaults,'horizon_hours':4},shared,Failing())
    service=Service(config,store=store,conditions=shared,astronomy=Astro(),detectors=[detector],sources={'hrrr':Failing()},adapters=[])
    try:
        service.tick(now)
        events=service.upcoming(now);assert events
        assert shared.refreshes==1
        identity={e['candidate']['id'] for e in events}
        assert all(e['candidate']['interest'] in (60,65) for e in events)
        assert all(e['candidate']['metadata']['microphysics'] is None for e in events)
        service.tick(now+timedelta(minutes=5))
        assert {e['candidate']['id'] for e in service.upcoming(now+timedelta(minutes=5))}==identity
    finally:service.close()


def mock_hrrr(store,config,now,status=206):
    rows=[];records=[];offset=0
    for level in (20,30):
        for field in FIELDS:
            rows.append(f'{len(rows)+1}:{offset}:d=2026100612:{field}:{level} hybrid level:3 hour fcst:')
            records.append(b'GRIB'+f'{field}{level}'.encode().ljust(12,b' ')+b'7777');offset+=20
    rows.append(f'{len(rows)+1}:{offset}:d=2026100612:OTHER:surface:3 hour fcst:')
    index='\n'.join(rows)
    calls=[]
    def handler(request):
        calls.append(request)
        if str(request.url).endswith('.idx'):return httpx.Response(200,text=index)
        start,end=map(int,request.headers['Range'][6:].split('-'))
        content=b''.join(records)[start:end+1]
        return httpx.Response(status,content=content,headers={'Content-Range':f'bytes {start}-{end}/10000'})
    def decoder(raw,observer,run,forecast,level):
        field=raw[4:16].decode().strip().removesuffix(str(level))
        return {**profile(now)['layers'][0]['fields'][field],'field':field}
    client=httpx.Client(transport=httpx.MockTransport(handler))
    source=HRRR(client,store,Observer(41.7,-73.8,137,'America/New_York'),{**HRRR.defaults,'enabled':True},['sky_crystals'],decoder)
    return source,client,calls,index


def test_hrrr_ranges_run_cache_restart_freshness_no_duplicate_bytes(store,config,now):
    source,client,calls,index=mock_hrrr(store,config,now)
    try:
        assert len(select_ranges(index,[20,30]))==14
        source.refresh(now)
        assert source.health.error is None
        assert source.health.valid_until==iso(now+timedelta(hours=1))
        assert len(calls)==30 # 2 indexes + 28 selected ranges, no whole files.
        assert all('Range' in call.headers for call in calls if not str(call.url).endswith('.idx'))
        extracted=source.at(now,now);assert extracted and len(extracted['layers'])==2
        source.refresh(now+timedelta(hours=1)) # Same synoptic run: no re-download.
        assert len(calls)==30 and source.health.retrieved_at==iso(now)
        other=HRRR(client,store,source.observer,source.settings,['sky_crystals'],source.decoder)
        other.refresh(now+timedelta(hours=1,minutes=5));assert len(calls)==30
        assert other.at(now,now+timedelta(hours=9)) is None
        assert other.at(now,now+timedelta(hours=1,seconds=1)) is None
        with pytest.raises(ValueError):select_ranges(index.replace('CIMIXR','NOT_ICE'),[20,30])
    finally:client.close()


def test_corrupt_cached_microphysics_units_and_values_rejected(now):
    data={'issued_at':iso(now-timedelta(hours=3)),'retrieved_at':iso(now),
          'samples':[profile(now)]}
    for field,point in data['samples'][0]['layers'][0]['fields'].items():point['field']=field
    HRRR.validate(data)
    data['samples'][0]['layers'][0]['fields']['CIMIXR']['value']=-.01
    with pytest.raises(ValueError):HRRR.validate(data)
    data['samples'][0]['layers'][0]['fields']['CIMIXR'].update(value=0,units='unknown')
    with pytest.raises(ValueError):HRRR.validate(data)


def test_opportunity_closes_at_geometric_limit(config,conditions,now):
    class Astro:
        def position(self,body,at,observer):
            altitude=20+(at-now).total_seconds()/60
            return {'altitude_deg':altitude,'azimuth_deg':100}
    class NoMicrophysics:
        def at(self,*args):return None
    detector=SkyCrystalDetector({**SkyCrystalDetector.defaults,'phenomena':['cza'],'horizon_hours':1},FakeConditions(conditions),NoMicrophysics())
    batch=detector.detect(DetectionContext(config.observer,now,1,Astro()))
    assert len(batch.candidates)==1
    assert batch.candidates[0].end<now+timedelta(minutes=12.2)
    assert possibility('cza',Astro().position('Sun',batch.candidates[0].end,config.observer)['altitude_deg'])


@pytest.mark.parametrize('status',[200,429,503])
def test_hrrr_full_file_rate_limit_outage_fallback_backoff(store,config,now,status):
    source,client,calls,index=mock_hrrr(store,config,now,status)
    try:
        source.refresh(now)
        assert source.health.error and source.at(now,now) is None
        count=len(calls)
        source.refresh(now+timedelta(minutes=5))
        assert len(calls)==count
    finally:client.close()


def test_frozen_real_grib_parameter_units_geometry():
    pytest.importorskip('eccodes')
    observer=Observer(41.697911,-73.810533,137,'America/New_York')
    run=parse('2026-10-10T12:00:00Z')
    point=decode_point(Path('tests/fixtures/hrrr-ice-level20.grib2').read_bytes(),observer,run,3,20)
    assert point['field']=='CIMIXR' and point['units']=='kg kg**-1' and point['value']==0
    assert point['distance_km']==pytest.approx(1.38770945,abs=.01)
    with pytest.raises(ValueError):decode_point(Path('tests/fixtures/hrrr-ice-level20.grib2').read_bytes(),observer,run,6,20)
