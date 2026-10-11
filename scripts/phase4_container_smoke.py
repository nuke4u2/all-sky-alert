"""Installed-package, offline Linux smoke for map/settings/optics and GRIB assets."""
from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time
import os

import httpx
import tomlkit
import uvicorn

from all_sky_alert.config import DEFAULTS,load
from all_sky_alert.domain import Conditions,Metric,Observer,iso,parse
from all_sky_alert.hrrr import decode_point
from all_sky_alert.service import Service
from all_sky_alert.web import create_app


fixtures=Path(sys.argv[1]);now=parse('2026-10-10T14:00:00Z')
observer=Observer(41.697911,-73.810533,137,'America/New_York')
decoded=decode_point((fixtures/'hrrr-ice-level20.grib2').read_bytes(),observer,
                     parse('2026-10-10T12:00:00Z'),3,20)
assert decoded['field']=='CIMIXR' and decoded['units']=='kg kg**-1'
payload=json.loads((fixtures/'adsb-radius.json').read_text());payload['now']=now.timestamp()*1000
for state in payload['ac']:
    if 'lat' in state:state.update(lat=observer.latitude+.01,lon=observer.longitude+.01)
class Shared:
    def refresh(self,at):pass
    def at(self,when,geo):
        return Conditions({name:Metric(value,'fixture',.8,'Synthetic QA','Fixture only',iso(when))
            for name,value in {'cloud_high':70,'cloud_low':5,'temperature_surface':5,'humidity':45,'wind_surface':1}.items()},
            'fresh',iso(now),None,valid_until=iso(now+timedelta(hours=6)))
with TemporaryDirectory(prefix='sky-phase4-') as private:
    values=deepcopy(DEFAULTS)
    for name,settings in values['detectors'].items():settings['enabled']=name in ('adsb','sky_crystals')
    values['service']['data_dir']='fixture-data';values['map']['tiles_enabled']=False
    path=Path(private)/'config.toml'
    path.write_text(tomlkit.dumps({'observer':asdict(observer),**values}),encoding='utf-8')
    service=Service(load(path),conditions=Shared(),adapters=[],clock=lambda:now)
    service.client.close();client=httpx.Client(transport=httpx.MockTransport(lambda request:
        httpx.Response(200,json=payload) if request.url.host=='api.adsb.lol' else (_ for _ in ()).throw(AssertionError('Unexpected acquisition'))))
    service.client=client
    for source in service.sources.values():source.client=client
    service.tick(now,deliver=False)
    rows=service.upcoming(now)
    assert any(row['candidate']['detector']=='sky_crystals' for row in rows)
    server=uvicorn.Server(uvicorn.Config(create_app(service,background=False),host='127.0.0.1',port=8022,proxy_headers=False,log_level='warning'))
    thread=Thread(target=server.run,daemon=True);thread.start()
    base='http://127.0.0.1:8022'
    try:
        with httpx.Client(timeout=5) as browser:
            for _ in range(50):
                try:
                    browser.get(base+'/api/health').raise_for_status();break
                except httpx.ConnectError:time.sleep(.1)
            routes=['/','/settings','/aircraft','/api/aircraft','/api/map-config',
                    '/static/vendor/leaflet/leaflet.js','/static/vendor/leaflet/LICENSE','/static/map.js','/static/settings.js']
            routes += ['/events/'+row['candidate']['id'] for row in rows]
            for route in routes:browser.get(base+route).raise_for_status()
            assert browser.get(base+'/api/aircraft').json()['aircraft']
            form=browser.get(base+'/settings').text
            csrf=re.search('data-csrf="([^"]+)"',form)[1];revision=re.search('data-revision="([^"]+)"',form)[1]
            browser.post(base+'/settings',headers={'Origin':base,'X-CSRF-Token':csrf},
                json={'revision':revision,'changes':{'map.radius_km':45}}).raise_for_status()
            assert load(path).values['map']['radius_km']==45 and service.config.values['map']['radius_km']==40
            assert 'Saved settings differ' in browser.get(base+'/settings').text
        print(json.dumps({'uid':os.getuid(),'routes_200':len(routes),'optics':True,'map':True,
            'atomic_settings':True,'decoder':decoded['decoder'],'ice_units':decoded['units'],
            'network':'none','gmail':False},indent=2))
    finally:
        server.should_exit=True;thread.join(10);service.close();assert not thread.is_alive()
