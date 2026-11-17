from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta

from fastapi.testclient import TestClient

from all_sky_alert.domain import Assessment
from all_sky_alert.service import Service
from all_sky_alert.web import create_app, sorted_predictions
from conftest import FakeConditions


def test_prediction_sort_options_and_unknown_quality(event,conditions,now):
    rows=[]
    for identifier,hour,interest,quality,priority in [
        ('early',1,95,0,1),('middle',2,40,None,100),('late',3,70,90,50)]:
        candidate=replace(event,id=identifier,start=now+timedelta(hours=hour),
            peak=now+timedelta(hours=hour+1),end=now+timedelta(hours=hour+2),interest=interest)
        rows.append({'candidate':candidate.to_dict(),
            'assessment':asdict(Assessment(quality,.8,{},[],conditions)),'priority':priority})
    original=deepcopy(rows)
    expected={'time_desc':['late','middle','early'],'time_asc':['early','middle','late'],
        'interest_desc':['early','late','middle'],'interest_asc':['middle','late','early'],
        'quality_desc':['late','early','middle'],'quality_asc':['early','late','middle'],
        'priority_desc':['middle','late','early']}
    for order,identifiers in expected.items():
        assert [p['candidate']['id'] for p in sorted_predictions(rows,order)]==identifiers
    # Equal scores fall back to actual event time, not database insertion order.
    tied=deepcopy(rows)
    for row in tied:row['candidate']['interest']=50
    assert [p['candidate']['id'] for p in sorted_predictions(tied,'interest_desc')]==['late','middle','early']
    typed=deepcopy(rows)
    for row,name in zip(typed,['sunset','planetary','aurora']):row['candidate']['detector']=name
    assert [p['candidate']['detector'] for p in sorted_predictions(typed,'type')]==['aurora','planetary','sunset']
    assert rows==original


def test_home_api_sort_recent_filters_and_validation(config,store,event,conditions,now):
    class Existing:
        name='planetary'
    service=Service(config,store=store,conditions=FakeConditions(conditions),detectors=[Existing()],
        sources={},adapters=[],clock=lambda:now)
    try:
        for suffix,offset,interest in [('early',1,95),('late',3,50),('distant',24*400,100),
                                      ('recent-early',-2,95),('recent-late',-1,50)]:
            start=now+timedelta(hours=offset)
            candidate=replace(event,id=suffix,title=suffix,start=start,peak=start+timedelta(minutes=10),
                end=start+timedelta(minutes=20),interest=interest)
            store.save({'candidate':candidate.to_dict(),
                'assessment':asdict(Assessment(80,.8,{},[],conditions)),
                'state':'EXPIRED' if offset<0 else 'READY','priority':interest},now,[],[])
        before=service.upcoming(now)
        with TestClient(create_app(service,background=False),base_url='http://localhost') as client:
            data=client.get('/api/predictions').json()
            assert [p['candidate']['id'] for p in data['upcoming']]==['early','late','distant']
            assert [p['candidate']['id'] for p in data['recent']]==['recent-early','recent-late']
            home=client.get('/').text
            assert '<option value="time_asc" selected>Event time: soonest first</option>' in home
            assert home.index('href="/events/early"')<home.index('href="/events/late"')<home.index('href="/events/distant"')
            response=client.get('/?filter=1&types=planetary&sort=interest_desc')
            assert response.status_code==200
            assert '<option value="interest_desc" selected>' in response.text
            assert response.text.index('href="/events/distant"')<response.text.index('href="/events/early"')<response.text.index('href="/events/late"')
            for order,expected in [('time_asc',['early','late','distant']),
                                   ('time_desc',['distant','late','early']),
                                   ('interest_desc',['distant','early','late'])]:
                assert [p['candidate']['id'] for p in client.get('/api/predictions?sort='+order).json()['upcoming']]==expected
            reverse=client.get('/?sort=time_desc').text
            assert '<option value="time_desc" selected>Event time: latest first</option>' in reverse
            assert reverse.index('href="/events/distant"')<reverse.index('href="/events/late"')<reverse.index('href="/events/early"')
            assert not client.get('/api/predictions?filter=1&sort=interest_desc').json()['upcoming']
            for path in ('/','/api/predictions','/api/live'):
                assert client.get(path+'?sort=invalid').status_code==422
            assert service.upcoming(now)==before # UI ordering changes no stored prediction/priority.
    finally:service.close()

