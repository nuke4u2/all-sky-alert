from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import re
import tomllib

import pytest
from fastapi.testclient import TestClient

from all_sky_alert.config import load,from_dict,DEFAULTS
from all_sky_alert.config_edit import ConfigEditor
from all_sky_alert.network import local_address
from all_sky_alert.service import Service
from all_sky_alert.web import create_app
from conftest import FakeConditions


@pytest.fixture
def configured(tmp_path):
    path=tmp_path/'config.toml'
    path.write_text('# personalized settings\n[observer]\nlatitude=41.7\nlongitude=-73.8\nelevation_m=137\ntimezone="America/New_York"\n[gmail]\nenabled=false\ndestination="user@example.org"\ntoken="private/custom-token.json"\nclient_secret="private/custom-client.json"\n')
    return load(path)


def test_config_sync_preserves_values_comments_and_is_idempotent(configured):
    editor=ConfigEditor(configured.file_path)
    editor.sync();first=configured.file_path.read_bytes();editor.sync()
    assert configured.file_path.read_bytes()==first
    saved=load(configured.file_path)
    assert saved.observer==configured.observer
    assert saved.values['gmail']==configured.values['gmail']
    assert not saved.values['detectors']['sky_crystals']['enabled']
    assert '# personalized settings' in first.decode()
    assert saved.values['map']['track_minutes']==10


def test_atomic_save_conflicts_invalid_and_replace_failure(configured,monkeypatch):
    editor=ConfigEditor(configured.file_path);_,revision,original=editor.read()
    with pytest.raises(ValueError):editor.save({'observer.latitude':0},revision)
    with pytest.raises(ValueError):editor.save({'map.track_minutes':500},revision)
    assert configured.file_path.read_bytes()==original
    def fail(*args):raise OSError('test replace failure')
    with monkeypatch.context() as patch:
        patch.setattr('all_sky_alert.config_edit.os.replace',fail)
        with pytest.raises(OSError):editor.save({'notifications.enabled':False},revision)
    assert configured.file_path.read_bytes()==original
    assert not list(configured.base.glob('.config-*.tmp'))
    editor.save({'notifications.enabled':False,'gmail.event_types':['adsb','iss']},revision)
    with pytest.raises(RuntimeError):editor.save({'gmail.enabled':True},revision)
    assert load(configured.file_path).values['gmail']['event_types']==['adsb','iss']
    assert load(configured.file_path).observer==configured.observer


def test_settings_csrf_save_restart_protected_size_and_malformed(configured,conditions):
    service=Service(configured,conditions=FakeConditions(conditions),detectors=[],sources={},adapters=[])
    try:
        with TestClient(create_app(service,background=False),base_url='http://localhost') as client:
            page=client.get('/settings');assert page.status_code==200
            assert 'private/custom-token' not in page.text and 'user@example.org' not in page.text
            csrf=re.search('data-csrf="([^"]+)"',page.text)[1]
            revision=re.search('data-revision="([^"]+)"',page.text)[1]
            body={'revision':revision,'changes':{'detectors.adsb.enabled':True,'gmail.event_types':['adsb'],'notifications.enabled':False}}
            assert client.post('/settings',json=body).status_code==403
            headers={'Origin':'http://evil.example','X-CSRF-Token':csrf}
            assert client.post('/settings',json=body,headers=headers).status_code==403
            headers['Origin']='http://localhost'
            assert client.post('/settings',json=body,headers={**headers,'X-CSRF-Token':'f'*64+'.'+'x'*64}).status_code==403
            before=configured.file_path.read_bytes()
            assert client.post('/settings',json={'revision':revision,'changes':{'gmail.token':'bad'}},headers=headers).status_code==422
            assert client.post('/settings',json={'revision':revision,'changes':{'map.radius_km':-1}},headers=headers).status_code==422
            assert client.post('/settings',content=b'{',headers={**headers,'Content-Type':'application/json'}).status_code==422
            assert client.post('/settings',content=b'x'*33000,headers={**headers,'Content-Type':'application/json'}).status_code==413
            assert configured.file_path.read_bytes()==before
            response=client.post('/settings',json=body,headers=headers)
            assert response.status_code==200 and response.json()['restart_required']
            assert configured.values['notifications']['enabled'] # Startup state unchanged.
            saved=load(configured.file_path)
            assert saved.values['detectors']['adsb']['enabled'] and saved.values['gmail']['event_types']==['adsb']
            assert saved.observer==configured.observer and saved.values['gmail']['token']=='private/custom-token.json'
            assert client.post('/settings',json=body,headers=headers).status_code==409
            assert 'differ from the running service' in client.get('/settings').text
    finally:service.close()


@pytest.mark.parametrize('address,valid',[('127.0.0.1',True),('192.168.1.40',True),('10.2.3.4',True),('::1',True),('fd00::1',True),('fe80::1%2',True),('::ffff:192.168.1.1',True),('8.8.8.8',False),('100.64.0.1',False),('192.0.2.1',False)])
def test_trusted_address_scope(address,valid):assert local_address(address)==valid


def test_lan_hosts_ipv6_and_public_client(configured,conditions):
    configured.values['service'].update(lan_access=True,bind_host='0.0.0.0',allowed_hosts=['localhost','127.0.0.1','::1','192.168.1.40','weissbier'])
    service=Service(configured,conditions=FakeConditions(conditions),detectors=[],sources={},adapters=[])
    try:
        app=create_app(service,background=False)
        with TestClient(app,base_url='http://192.168.1.40:8008',client=('192.168.1.60',50000)) as client:
            assert client.get('/api/health').status_code==200
            assert client.get('/api/health',headers={'Host':'localhost:8008'}).status_code==200
            assert client.get('/api/health',headers={'Host':'[::1]:8008'}).status_code==200
            assert client.get('/api/health',headers={'Host':'Weissbier:8008'}).status_code==200
            assert client.get('/api/health',headers={'Host':'evil.example'}).status_code==400
            assert client.get('/api/health',headers={'Host':'user@localhost'}).status_code==400
            assert client.get('/api/health',headers={'Host':'evil.example','X-Forwarded-Host':'localhost'}).status_code==400
        with TestClient(app,base_url='http://localhost',client=('8.8.8.8',50000)) as client:
            assert client.get('/api/health').status_code==403
    finally:service.close()


@pytest.mark.parametrize('patch',[{'service':{'allowed_hosts':['*.example']}},{'service':{'bind_host':'0.0.0.0'}},{'service':{'lan_access':True,'bind_host':'8.8.8.8'}},{'service':{'trusted_proxy_ips':['*']}},{'gmail':{'event_types':['unknown']}},{'map':{'tile_url':'https://user:key@tiles.example/{z}/{x}/{y}.png'}}])
def test_new_config_security_validation(configured,patch):
    raw=tomllib.loads(configured.file_path.read_text())
    for section,value in patch.items():raw.setdefault(section,{}).update(value)
    with pytest.raises(ValueError):from_dict(raw,configured.base)
