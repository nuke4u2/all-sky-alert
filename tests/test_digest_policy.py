from dataclasses import asdict, replace
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor
import re
import sqlite3
import tomllib

import pytest
from fastapi.testclient import TestClient

from all_sky_alert.cli import main
from all_sky_alert.config import from_dict, load
from all_sky_alert.config_edit import ConfigEditor
from all_sky_alert.digests import build, dispatch, identity, wall_time
from all_sky_alert.domain import Assessment, Conditions, DetectionBatch, Observer, iso, parse
from all_sky_alert.mail_policy import immediate_allowed
from all_sky_alert.notifications import SafeDeliveryError, UncertainDeliveryError, render_message
from all_sky_alert.policy import priority
from all_sky_alert.service import Service
from all_sky_alert.storage import Store
from all_sky_alert.web import create_app
from conftest import FakeConditions, RecordingAdapter


@pytest.fixture
def mail_config(config):
    result=replace(config,observer=Observer(41.7,-73.8,137,'America/New_York'))
    result.values['gmail']['enabled']=True
    result.values['gmail']['daily_digest']['enabled']=True
    result.values['gmail']['weekly_digest']['enabled']=True
    result.values['detectors']['eclipses']['enabled']=True
    return result


def save(store,config,event,conditions,*,quality=95,now=None):
    event=replace(event,observer_key=config.observer.key)
    assessed=Assessment(quality,.8,{},['Fixture'],conditions,True,conditions.valid_until)
    now=now or event.start-timedelta(hours=2)
    store.save({'candidate':event.to_dict(),'assessment':asdict(assessed),'state':'READY',
                'priority':priority(event,assessed)},now,[],[])
    return event


def test_below_threshold_still_local_and_daily(mail_config,store,event,conditions):
    now=parse('2026-10-27T11:00:00Z')
    event=replace(event,observer_key=mail_config.observer.key,start=now+timedelta(hours=2),
        peak=now+timedelta(hours=3),end=now+timedelta(hours=4),interest=45)
    class Detector:
        name='planetary'
        def detect(self,ctx):return DetectionBatch([event],{event.scope})
    adapter=RecordingAdapter();adapter.name='gmail'
    service=Service(mail_config,store=store,conditions=FakeConditions(conditions),
        detectors=[Detector()],sources={},adapters=[adapter])
    try:
        service.tick(now)
        detail=store.detail(event.id,mail_config.observer.key)
        assert detail['alerts'] and not detail['deliveries']
        assert not adapter.alerts
        service.dispatch(now+timedelta(seconds=15))
        assert len(adapter.alerts)==1 and adapter.alerts[0]['kind']=='digest'
        assert adapter.alerts[0]['items'][0]['interest']==45
    finally:service.close()


@pytest.mark.parametrize('interest,expected',[(84,False),(85,True),(95,True)])
def test_immediate_floor_and_family_selection(mail_config,event,interest,expected):
    assert immediate_allowed(mail_config.values['gmail'],replace(event,interest=interest))==expected
    mail_config.values['gmail']['immediate']['event_types']=['adsb']
    assert not immediate_allowed(mail_config.values['gmail'],replace(event,interest=100))


def test_poor_weather_exception_digest_then_imminent(mail_config,store,event,conditions):
    now=parse('2026-10-27T11:00:00Z')
    event=replace(event,observer_key=mail_config.observer.key,detector='eclipses',
        kind='eclipse.solar.total',profile='eclipse',interest=100,
        start=now+timedelta(hours=2),peak=now+timedelta(hours=3),end=now+timedelta(hours=4))
    save(store,mail_config,event,conditions,quality=25,now=now)
    adapter=RecordingAdapter();adapter.name='gmail'
    dispatch(mail_config,store,adapter,now)
    assert len(adapter.alerts)==1 and adapter.alerts[0]['items'][0]['quality']==25
    assert 'poor or unknown' in adapter.alerts[0]['items'][0]['section']
    service=Service(mail_config,store=store,conditions=FakeConditions(conditions),
        detectors=[],sources={},adapters=[adapter])
    service.detectors=[type('Existing',(),{'name':'eclipses'})()]
    try:
        service.dispatch(event.start-timedelta(minutes=30))
        immediate=[message for message in adapter.alerts if message.get('kind')!='digest']
        assert len(immediate)==1 and immediate[0]['stage']=='imminent'
        service.dispatch(event.start-timedelta(minutes=29));assert len(adapter.alerts)==2
    finally:service.close()


@pytest.mark.parametrize('day,hours',[(date(2026,3,8),23),(date(2026,11,1),25)])
def test_daily_local_calendar_and_cross_midnight(mail_config,store,event,conditions,day,hours):
    start=wall_time(day,'00:00',mail_config.observer.timezone)
    end=wall_time(day+timedelta(days=1),'00:00',mail_config.observer.timezone)
    assert (end-start).total_seconds()==hours*3600
    crossing=replace(event,id='cross',start=start-timedelta(hours=1),
        peak=start+timedelta(hours=1),end=start+timedelta(hours=2))
    save(store,mail_config,crossing,conditions,now=start)
    payload=build(mail_config,store,'daily_digest',day,start)
    assert [row['id'] for row in payload['items']]==[crossing.id]
    assert parse(payload['window_start'])==start and parse(payload['window_end'])==end
    outside=replace(crossing,id='outside',start=end,peak=end+timedelta(hours=1),end=end+timedelta(hours=2))
    save(store,mail_config,outside,conditions,now=start)
    assert outside.id not in [row['id'] for row in build(mail_config,store,'daily_digest',day,start)['items']]


def test_weekly_strict_range_interest_horizon_and_no_weather(mail_config,store,event):
    day=date(2026,10,25);now=wall_time(day,'18:00',mail_config.observer.timezone)
    lower=wall_time(day+timedelta(days=7),'18:00',mail_config.observer.timezone)
    upper=wall_time(day+timedelta(days=90),'18:00',mail_config.observer.timezone)
    for identifier,start,interest,detector in [('boundary',lower,100,'eclipses'),
        ('major',lower+timedelta(seconds=1),80,'eclipses'),('minor',lower+timedelta(days=1),69,'eclipses'),
        ('last',upper,95,'eclipses'),('too-far',upper+timedelta(seconds=1),100,'eclipses'),
        ('not-credible',lower+timedelta(days=1),100,'adsb')]:
        row=replace(event,id=identifier,detector=detector,interest=interest,
            start=start,peak=start+timedelta(hours=1),end=start+timedelta(hours=2))
        save(store,mail_config,row,Conditions({},'outside_forecast',None,None),quality=100 if identifier=='major' else None,now=now)
    payload=build(mail_config,store,'weekly_digest',day,now)
    assert {i['id'] for i in payload['items']}=={'major','last'}
    assert payload['items'][0]['id']=='last' # Weekly importance is independent of old weather priority.
    assert all(i['quality'] is None for i in payload['items'])
    assert any('730 days' in note for note in payload['notes'])
    assert mail_config.values['service']['prediction_days']==2


@pytest.mark.parametrize('day,clock,expected',[(date(2026,3,8),'02:30','2026-03-08T07:00:00Z'),
    (date(2026,11,1),'01:30','2026-11-01T05:30:00Z')])
def test_dst_schedule_resolution(day,clock,expected):
    assert wall_time(day,clock,'America/New_York')==parse(expected)


def test_dst_fall_restart_and_atomic_duplicate_claim(mail_config,store,event,conditions):
    mail_config.values['gmail']['daily_digest']['time']='01:30'
    day=date(2026,11,1);first=parse('2026-11-01T05:30:00Z');second=parse('2026-11-01T06:30:00Z')
    event=replace(event,start=second+timedelta(hours=2),peak=second+timedelta(hours=3),end=second+timedelta(hours=4))
    save(store,mail_config,event,conditions,now=first)
    payload=build(mail_config,store,'daily_digest',day,first)
    assert store.enqueue_digest(payload,mail_config.observer.key,first+timedelta(hours=4),first)
    assert not store.enqueue_digest(payload,mail_config.observer.key,first+timedelta(hours=4),first)
    with ThreadPoolExecutor(2) as pool:
        claimed=list(pool.map(lambda _:Store(store.path).claim_digest(mail_config.observer.key,first),range(2)))
    assert sum(job is not None for job in claimed)==1
    store.digest_result(payload['id'],'SENT',first,1,remote_id='accepted')
    adapter=RecordingAdapter()
    dispatch(mail_config,Store(store.path),adapter,second)
    assert not adapter.alerts


def test_weekly_restart_and_iso_period_dedup(mail_config,store,event,conditions):
    day=date(2026,10,25);now=wall_time(day,'18:00',mail_config.observer.timezone)
    start=now+timedelta(days=20)
    save(store,mail_config,replace(event,detector='eclipses',interest=95,start=start,
        peak=start+timedelta(hours=1),end=start+timedelta(hours=2)),conditions,quality=None,now=now)
    adapter=RecordingAdapter()
    dispatch(mail_config,store,adapter,now);dispatch(mail_config,Store(store.path),adapter,now+timedelta(hours=1))
    assert len([p for p in adapter.alerts if p['mode']=='weekly_digest'])==1
    assert identity(mail_config,'weekly_digest',date(2026,10,19))==identity(mail_config,'weekly_digest',day)


def test_restart_before_after_due_and_late_catchup(mail_config,store,event,conditions):
    now=parse('2026-10-27T10:59:00Z');day=date(2026,10,27)
    save(store,mail_config,replace(event,start=now+timedelta(hours=4),peak=now+timedelta(hours=5),
        end=now+timedelta(hours=6)),conditions,now=now)
    adapter=RecordingAdapter()
    dispatch(mail_config,store,adapter,now);assert not adapter.alerts
    dispatch(mail_config,Store(store.path),adapter,now+timedelta(hours=1));assert len(adapter.alerts)==1
    dispatch(mail_config,Store(store.path),adapter,now+timedelta(hours=2));assert len(adapter.alerts)==1
    nextday=day+timedelta(days=1);late=wall_time(nextday,'12:00',mail_config.observer.timezone)
    dispatch(mail_config,store,adapter,late)
    assert store.digest(identity(mail_config,'daily_digest',nextday)[0])['status']=='EXPIRED'
    assert len(adapter.alerts)==1


@pytest.mark.parametrize('error,state,retry',[(SafeDeliveryError('rejected'),'FAILED',True),
    (UncertainDeliveryError('ambiguous'),'UNKNOWN',False),(RuntimeError('untrusted'),'UNKNOWN',False)])
def test_safe_and_ambiguous_delivery_restart_retry(mail_config,store,event,conditions,error,state,retry):
    now=parse('2026-10-27T11:00:00Z');day=date(2026,10,27)
    save(store,mail_config,replace(event,start=now+timedelta(hours=2),peak=now+timedelta(hours=3),
        end=now+timedelta(hours=4)),conditions,now=now)
    adapter=RecordingAdapter();adapter.error=error
    dispatch(mail_config,store,adapter,now);key=identity(mail_config,'daily_digest',day)[0]
    assert store.digest(key)['status']==state
    dispatch(mail_config,Store(store.path),adapter,now+timedelta(seconds=30));assert store.digest(key)['attempts']==1
    adapter.error=None
    dispatch(mail_config,Store(store.path),adapter,now+timedelta(minutes=3))
    assert len(adapter.alerts)==int(retry)
    if not retry:
        assert store.retry_digest(key,mail_config.observer.key,now+timedelta(minutes=4))
        dispatch(mail_config,store,adapter,now+timedelta(minutes=4));assert len(adapter.alerts)==1
    assert store.digest(key)['status']=='SENT'


def test_interrupted_send_unknown_and_expired_scientific_inputs(mail_config,store,event,conditions):
    now=parse('2026-10-27T11:00:00Z');day=date(2026,10,27)
    event=replace(event,start=now+timedelta(hours=2),peak=now+timedelta(hours=3),end=now+timedelta(hours=4))
    save(store,mail_config,event,conditions,now=now)
    payload=build(mail_config,store,'daily_digest',day,now)
    store.enqueue_digest(payload,mail_config.observer.key,now+timedelta(hours=4),now)
    job=store.claim_digest(mail_config.observer.key,now);assert job
    reopened=Store(store.path);reopened.recover_deliveries()
    assert reopened.digest(job['id'])['status']=='UNKNOWN'
    assert not reopened.claim_digest(mail_config.observer.key,now+timedelta(minutes=5))
    assert reopened.retry_digest(job['id'],mail_config.observer.key,now+timedelta(minutes=5))
    save(store,mail_config,replace(event,valid_until=iso(now+timedelta(minutes=1))),conditions,now=now+timedelta(minutes=2))
    adapter=RecordingAdapter();dispatch(mail_config,reopened,adapter,now+timedelta(minutes=5))
    assert not adapter.alerts and reopened.digest(job['id'])['status']=='EMPTY'


def test_legacy_migration_preserves_values_comments_and_explicit_modes(tmp_path):
    path=tmp_path/'config.toml'
    path.write_text('# preserve me\n[observer]\nlatitude=41.7\nlongitude=-73.8\ntimezone="America/New_York"\n'
        '[gmail]\nenabled=true\ndestination="user@example.org"\ntoken="private/custom-token.json"\n'
        'client_secret="private/custom-client.json"\nevent_types=["eclipses","planetary"]\n'
        '[gmail.weekly_digest]\nevent_types=["eclipses"]\n')
    loaded=load(path)
    assert loaded.values['gmail']['immediate']['event_types']==['eclipses','planetary']
    assert loaded.values['gmail']['daily_digest']['event_types']==['eclipses','planetary']
    assert loaded.values['gmail']['weekly_digest']['event_types']==['eclipses']
    ConfigEditor(path).sync();first=path.read_bytes();ConfigEditor(path).sync()
    assert path.read_bytes()==first and '# preserve me' in path.read_text()
    after=load(path);assert after.observer==loaded.observer and after.values['gmail']==loaded.values['gmail']
    raw=tomllib.loads(path.read_text());assert 'event_types' not in raw['gmail']
    assert raw['gmail']['token']=='private/custom-token.json' and raw['gmail']['destination']=='user@example.org'


@pytest.mark.parametrize('table,patch',[('immediate',{'minimum_interest':101}),
    ('daily_digest',{'time':'25:00'}),('daily_digest',{'time':'7:00'}),('weekly_digest',{'weekday':'Sundae'}),
    ('weekly_digest',{'start_days_ahead':6}),('weekly_digest',{'end_days_ahead':7}),
    ('daily_digest',{'event_types':[['bad']]}),('daily_digest',{'max_events':True}),('daily_digest',{'catch_up_hours':0})])
def test_invalid_mode_config_rejected(tmp_path,table,patch):
    with pytest.raises(ValueError):
        from_dict({'observer':{'latitude':0,'longitude':0,'timezone':'UTC'},'gmail':{table:patch}},tmp_path)


def test_settings_modes_and_status_do_not_send(tmp_path,conditions):
    path=tmp_path/'config.toml';path.write_text('[observer]\nlatitude=0\nlongitude=0\ntimezone="America/New_York"\n')
    config=load(path)
    service=Service(config,conditions=FakeConditions(conditions),detectors=[],sources={},adapters=[],
        clock=lambda:parse('2026-10-27T12:00:00Z'))
    try:
        with TestClient(create_app(service,background=False),base_url='http://localhost') as client:
            page=client.get('/settings');assert page.status_code==200
            assert 'America/New_York' in page.text and page.text.count('data-types-field=')==3
            headers={'Origin':'http://localhost','X-CSRF-Token':re.search('data-csrf="([^"]+)"',page.text)[1]}
            changes={'notifications.minimum_confidence':.4,'notifications.exceptional_interest':92,
                'gmail.immediate.minimum_interest':87,'gmail.immediate.event_types':['adsb'],
                'gmail.daily_digest.enabled':True,'gmail.daily_digest.time':'08:15','gmail.daily_digest.event_types':['planetary','sky_crystals'],
                'gmail.weekly_digest.enabled':True,'gmail.weekly_digest.weekday':'Saturday','gmail.weekly_digest.time':'17:00',
                'gmail.weekly_digest.minimum_interest':75,'gmail.weekly_digest.start_days_ahead':8,
                'gmail.weekly_digest.end_days_ahead':120,'gmail.weekly_digest.event_types':['eclipses']}
            response=client.post('/settings',headers=headers,json={
                'revision':re.search('data-revision="([^"]+)"',page.text)[1],'changes':changes})
            assert response.status_code==200 and response.json()['restart_required']
            saved=load(path).values['gmail']
            assert load(path).values['notifications']['minimum_confidence']==.4
            assert saved['daily_digest']['time']=='08:15' and saved['weekly_digest']['end_days_ahead']==120
            assert saved['immediate']['event_types']==['adsb'] and not config.values['gmail']['daily_digest']['enabled']
            assert client.get('/api/notifications').status_code==200
            assert 'destination' not in client.get('/api/notifications').text
            assert not service.store.digest_history(config.observer.key,'daily_digest')
    finally:service.close()


def test_external_text_solar_safety_and_cli_preview_readonly(mail_config,store,event,conditions,tmp_path,monkeypatch,capsys):
    now=parse('2026-10-27T11:00:00Z')
    event=replace(event,title='<script>evil</script>\r\nBcc: bad@example.org',interest=100,
        start=now+timedelta(hours=2),peak=now+timedelta(hours=3),end=now+timedelta(hours=4),
        metadata={'safety':{'text':'Never view the Sun through unfiltered optics.','url':'https://science.nasa.gov/eclipses/safety/'}})
    save(store,mail_config,event,conditions,now=now)
    payload=build(mail_config,store,'daily_digest',date(2026,10,27),now)
    message=render_message(payload,'recipient@example.org')
    assert message.get_content_type()=='text/plain' and message['Bcc'] is None
    assert '<script>evil</script>' in message.get_content() and 'SOLAR VIEWING SAFETY' in message.get_content()
    assert 'All predictions:' not in message.get_content()
    with store.connection() as db:db.execute('PRAGMA user_version=3')
    readonly=Store.open_readonly(store.path)
    assert build(mail_config,readonly,'daily_digest',date(2026,10,27),now)==payload
    with readonly.connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==3
        with pytest.raises(sqlite3.OperationalError):db.execute('DELETE FROM predictions')
    path=tmp_path/'preview.toml'
    path.write_text('[observer]\nlatitude=41.7\nlongitude=-73.8\nelevation_m=137\ntimezone="America/New_York"\n[service]\ndata_dir="."\n')
    target=tmp_path/'all-sky-alert.sqlite3'
    with store.connection() as source,sqlite3.connect(target) as destination:source.backup(destination)
    monkeypatch.setattr('all_sky_alert.cli.utcnow',lambda:now)
    monkeypatch.setattr('all_sky_alert.cli.Service',lambda *a,**k:(_ for _ in ()).throw(AssertionError('Preview must not initialize service')))
    monkeypatch.setattr('sys.argv',['all-sky-alert','--config',str(path),'preview-daily-digest','--date','2026-10-27'])
    main();output=capsys.readouterr().out
    assert 'Preview only' in output and '<script>evil</script>' in output
    with sqlite3.connect(target) as db:assert db.execute('PRAGMA user_version').fetchone()[0]==3
    assert not (tmp_path/'secrets').exists()


def test_per_mode_filters_horizon_cap_and_stale_quality(mail_config,store,event,conditions):
    day=date(2026,10,25);now=wall_time(day,'18:00',mail_config.observer.timezone)
    start=now+timedelta(days=40)
    future=replace(event,detector='eclipses',interest=95,start=start,peak=start+timedelta(hours=1),end=start+timedelta(hours=2))
    stale=replace(conditions,valid_until=iso(now-timedelta(minutes=1)))
    save(store,mail_config,future,stale,now=now)
    weekly=build(mail_config,store,'weekly_digest',day,now)
    assert len(weekly['items'])==1 and weekly['items'][0]['quality'] is None
    mail_config.values['gmail']['weekly_digest']['event_types']=['planetary']
    assert not build(mail_config,store,'weekly_digest',day,now)['items']
    mail_config.values['gmail']['weekly_digest']['event_types']=['eclipses']
    mail_config.values['detectors']['eclipses']['horizon_days']=30
    assert not build(mail_config,store,'weekly_digest',day,now)['items']
    assert mail_config.values['gmail']['immediate']['event_types']==['*']


def test_five_attempt_ceiling_and_expired_manual_retry(mail_config,store,event,conditions):
    day=date(2026,10,27);now=wall_time(day,'07:00',mail_config.observer.timezone)
    save(store,mail_config,replace(event,start=now+timedelta(hours=3),peak=now+timedelta(hours=4),end=now+timedelta(hours=5)),conditions,now=now)
    adapter=RecordingAdapter();adapter.error=SafeDeliveryError('rejected')
    for minutes in (0,3,8,17,34,70):
        dispatch(mail_config,store,adapter,now+timedelta(minutes=minutes))
    key=identity(mail_config,'daily_digest',day)[0]
    assert store.digest(key)['attempts']==5 and store.digest(key)['status']=='FAILED'
    from all_sky_alert.digests import status
    daily_status=next(row for row in status(mail_config,store,now+timedelta(minutes=70)) if row['mode']=='daily_digest')
    assert parse(daily_status['next_at'])==wall_time(day+timedelta(days=1),'07:00',mail_config.observer.timezone)
    assert not store.retry_digest(key,mail_config.observer.key,now+timedelta(hours=5))
    dispatch(mail_config,store,adapter,now+timedelta(hours=5))
    assert store.digest(key)['status']=='EXPIRED'


def test_disabled_mode_pending_canceled_and_reenable_no_replay(mail_config,store,event,conditions):
    now=parse('2026-10-27T11:00:00Z');day=date(2026,10,27)
    save(store,mail_config,replace(event,start=now+timedelta(hours=2),peak=now+timedelta(hours=3),end=now+timedelta(hours=4)),conditions,now=now)
    payload=build(mail_config,store,'daily_digest',day,now)
    store.enqueue_digest(payload,mail_config.observer.key,now+timedelta(hours=4),now)
    mail_config.values['gmail']['daily_digest']['enabled']=False
    adapter=RecordingAdapter();dispatch(mail_config,store,adapter,now)
    assert store.digest(payload['id'])['status']=='CANCELED'
    mail_config.values['gmail']['daily_digest']['enabled']=True
    dispatch(mail_config,store,adapter,now+timedelta(minutes=1))
    assert not adapter.alerts


def test_weekly_late_catchup_and_stale_evaluating_record(mail_config,store,event,conditions):
    day=date(2026,10,25);due=wall_time(day,'18:00',mail_config.observer.timezone)
    start=due+timedelta(days=20)
    save(store,mail_config,replace(event,detector='eclipses',interest=95,start=start,peak=start+timedelta(hours=1),end=start+timedelta(hours=2)),conditions,now=due)
    store.health('worker','evaluating',due-timedelta(days=30),'Old interrupted CLI run')
    store.health('worker:slow','ok',due,'Normal evaluation complete')
    adapter=RecordingAdapter();dispatch(mail_config,store,adapter,due+timedelta(hours=12))
    assert len(adapter.alerts)==1 and adapter.alerts[0]['mode']=='weekly_digest'
    assert adapter.alerts[0]['day']==day.isoformat()


def test_base_url_validation_and_explicit_link(mail_config,store,event,conditions,tmp_path):
    now=parse('2026-10-27T11:00:00Z')
    save(store,mail_config,replace(event,start=now+timedelta(hours=2),peak=now+timedelta(hours=3),end=now+timedelta(hours=4)),conditions,now=now)
    mail_config.values['gmail']['base_url']='http://localhost:8008'
    text=render_message(build(mail_config,store,'daily_digest',date(2026,10,27),now),'user@example.org').get_content()
    assert 'http://localhost:8008/events/' in text
    for url in ('javascript:evil','https://user:key@localhost/','https://not-allowed.example/'):
        with pytest.raises(ValueError):
            from_dict({'observer':{'latitude':0,'longitude':0,'timezone':'UTC'},'gmail':{'base_url':url}},tmp_path)


def test_digest_schema_upgrade_preserves_event_and_milestone(store,mail_config,event,conditions):
    save(store,mail_config,event,conditions)
    original=store.detail(event.id,mail_config.observer.key)
    with store.connection() as db:
        db.execute('DROP TABLE digest_deliveries')
        db.execute('PRAGMA user_version=3')
    reopened=Store(store.path)
    assert reopened.detail(event.id,mail_config.observer.key)==original
    with reopened.connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==4
        assert db.execute("SELECT name FROM sqlite_master WHERE name='digest_deliveries'").fetchone()


def test_digest_retains_provider_credits_and_labels_expired_seeing(mail_config,store,event,conditions):
    from all_sky_alert.domain import Metric
    now=parse('2026-10-27T11:00:00Z')
    seeing=Metric(5,'index',.8,'ECCC RDPS','Native ordinal category',iso(now),
        issued_at=iso(now-timedelta(hours=6)),valid_until=iso(now-timedelta(seconds=1)))
    metrics={**conditions.metrics,'seeing_index':seeing,
        'cloud_total':replace(conditions.metrics['cloud_total'],source='Open-Meteo')}
    conditions=replace(conditions,metrics=metrics,valid_until=iso(now-timedelta(seconds=1)))
    event=replace(event,interest=100,start=now+timedelta(hours=2),peak=now+timedelta(hours=3),end=now+timedelta(hours=4))
    save(store,mail_config,event,conditions,now=now)
    payload=build(mail_config,store,'daily_digest',date(2026,10,27),now)
    assert payload['items'][0]['quality'] is None
    text=render_message(payload,'user@example.org').get_content()
    assert 'Excellent (5/5); stale snapshot' in text
    assert 'CC BY 4.0' in text and 'ECCC Data Services End-use Licence' in text
    assert 'Sources / provenance:' in text



