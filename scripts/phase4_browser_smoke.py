"""Private fixture-only mobile/LAN QA. Requires the qa extra and a browser.

No production data, credentials, upstream requests or mail. Optional --lan-host
tests this computer's private IPv4 interface without changing firewall settings.
All generated forecasts here are rendering fixtures, never production predictions.
"""
from argparse import ArgumentParser
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
import json
import os
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import time

import httpx
import tomlkit
import uvicorn
from playwright.sync_api import expect, sync_playwright

from all_sky_alert.assessment import derive
from all_sky_alert.config import DEFAULTS, load
from all_sky_alert.domain import Conditions, DetectionContext, Metric, Observer, iso, parse
from all_sky_alert.eclipse import EclipseDetector
from all_sky_alert.jupiter import JupiterDetector
from all_sky_alert.network import local_address
from all_sky_alert.policy import lifecycle, priority
from all_sky_alert.satellites import ISSTransitDetector
from all_sky_alert.service import Service
from all_sky_alert.web import create_app


class SharedFixture:
    def __init__(self, now):
        values={'cloud_total':5,'cloud_low':5,'cloud_mid':50,'cloud_high':70,
                'humidity':45,'visibility':40000,'wind_surface':1,'wind_250hpa':10,
                'temperature_surface':-25}
        self.value=derive(Conditions({name:Metric(value,'fixture units',.8,'Synthetic QA',
            'Fixture, not an operational forecast',iso(now)) for name,value in values.items()},
            'fresh',iso(now),None,valid_until=iso(now+timedelta(days=5))))
    def refresh(self, now):pass
    def at(self, when, geometry):return self.value


def main():
    parser=ArgumentParser();parser.add_argument('--lan-host');parser.add_argument('--port',type=int,default=8021)
    parser.add_argument('--channel',default='msedge' if os.name=='nt' else None)
    args=parser.parse_args()
    if args.lan_host and not local_address(args.lan_host):parser.error('Private LAN IP required')
    output=Path('test-artifacts/phase4-browser');output.mkdir(parents=True,exist_ok=True)
    fixtures=Path('tests/fixtures')
    omm=json.loads((fixtures/'iss-omm.json').read_text())
    now=parse(omm[0]['EPOCH']+'Z')
    aircraft=json.loads((fixtures/'adsb-radius.json').read_text());aircraft['now']=now.timestamp()*1000
    aircraft['ac'][0]['flight']='<img src=x onerror=evil>'
    ovation=json.loads((fixtures/'ovation-sample.json').read_text())
    cells={(lon,lat):value for lon,lat,value in ovation['coordinates']}
    ovation['coordinates']=[[lon,lat,cells.get((lon,lat),0)] for lon in range(360) for lat in range(-90,91)]
    ovation['Observation Time']=iso(now);ovation['Forecast Time']=iso(now+timedelta(minutes=15))
    def handler(request):
        if request.url.host=='celestrak.org':return httpx.Response(200,json=omm)
        if request.url.host=='api.adsb.lol':return httpx.Response(200,json=aircraft)
        if request.url.host=='services.swpc.noaa.gov':return httpx.Response(200,json=ovation)
        raise AssertionError('Unexpected source acquisition: '+request.url.host)
    with TemporaryDirectory(prefix='sky-phase4-') as private:
        values=deepcopy(DEFAULTS)
        for settings in values['detectors'].values():settings['enabled']=True
        values['service'].update(data_dir='fixture-data',prediction_days=2,bind_host='0.0.0.0',lan_access=True)
        values['service']['allowed_hosts'] += [args.lan_host] if args.lan_host else []
        values['map']['tiles_enabled']=False
        values['gmail']['enabled']=False
        values['detectors']['iss_transits']['horizon_hours']=1
        values['detectors']['planetary'].update(minimum_altitude_deg=0,maximum_sun_altitude_deg=0)
        values['detectors']['sky_crystals']['horizon_hours']=48
        path=Path(private)/'config.toml'
        path.write_text(tomlkit.dumps({'observer':asdict(Observer(51.4779,0,46,'Europe/London')),**values}),encoding='utf-8')
        shared=SharedFixture(now)
        service=Service(load(path),conditions=shared,adapters=[],clock=lambda:now)
        service.client.close();client=httpx.Client(transport=httpx.MockTransport(handler));service.client=client
        for source in service.sources.values():source.client=client
        service.tick(now,deliver=False)
        # Include solar eclipse and transit rendering branches using real frozen
        # reference geometry from other dates/sites; clearly fixture-only storage.
        eclipse=EclipseDetector({**EclipseDetector.defaults,'horizon_days':2}).detect(
            DetectionContext(Observer(32.7767,-96.797,130,'America/Chicago'),parse('2024-04-07T00:00:00Z'),2,service.astronomy)).candidates[0]
        center=json.loads((fixtures/'iss-transit-reference.json').read_text())[0]
        transit=ISSTransitDetector({**ISSTransitDetector.defaults,'horizon_hours':1},service.sources['orbits']).detect(
            DetectionContext(Observer(**center['observer']),parse(center['center'])-timedelta(minutes=10),1,service.astronomy)).candidates[0]
        # Jupiter rendering also uses real frozen geometry from a date with
        # visible activity, independently of the orbital fixture's epoch.
        jovian=JupiterDetector(values['detectors']['jupiter']).detect(
            DetectionContext(service.config.observer,parse('2026-10-28T00:00:00Z'),7,service.astronomy)).candidates
        jupiter=next(e for e in jovian if e.kind=='jupiter.overlap')
        for candidate in (eclipse,transit,jupiter):
            event=replace(candidate,observer_key=service.config.observer.key)
            assessment=service.profiles[event.profile](event,shared.value)
            service.store.save({'candidate':event.to_dict(),'assessment':asdict(assessment),
                'state':lifecycle(event,assessment,now,service.config.policy(event.kind)),'priority':priority(event,assessment)},now,[],[])
        # Force only the rendering context's Sun below twilight for the otherwise
        # real NOAA synthetic-grid-to-horizon aurora calculation.
        class NightGeometry:
            def position(self,body,at,observer):
                return {**service.astronomy.position(body,at,observer),'altitude_deg':-15 if body=='Sun' else 20}
            def moon_illumination(self,at):return .2
        aurora=next(d for d in service.detectors if d.name=='aurora').detect(
            DetectionContext(service.config.observer,now,1,NightGeometry())).candidates[0]
        assessment=service.profiles[aurora.profile](aurora,shared.value)
        service.store.save({'candidate':aurora.to_dict(),'assessment':asdict(assessment),
            'state':'LIKELY','priority':priority(aurora,assessment)},now,[],[])
        rows=service.store.predictions(service.config.observer.key,service.detector_names,now,24*365)
        ids={row['candidate']['detector']:row['candidate']['id'] for row in rows}
        # Historical fixture eclipse/transit can fall outside normal history query.
        ids.update(eclipses=eclipse.id,iss_transits=transit.id,aurora=aurora.id,jupiter=jupiter.id)
        assert set(service.detector_names)<=set(ids),ids
        server=uvicorn.Server(uvicorn.Config(create_app(service,background=False),host='0.0.0.0',
            port=args.port,proxy_headers=False,log_level='warning'))
        thread=Thread(target=server.run,daemon=True);thread.start()
        report={'fixture_only':True,'gmail':False,'online_tiles':False,'viewports':[], 'details':list(ids)}
        base=f'http://127.0.0.1:{args.port}'
        try:
            with httpx.Client(timeout=5,trust_env=False) as probe:
                for _ in range(60):
                    try:
                        probe.get(base+'/api/health').raise_for_status();break
                    except httpx.ConnectError:time.sleep(.1)
                report['localhost_status']=probe.get(base+'/').status_code
                if args.lan_host:
                    report['lan_url']=f'http://{args.lan_host}:{args.port}'
                    report['lan_status']=probe.get(report['lan_url']+'/').status_code
                    assert report['lan_status']==200
                assert probe.get(base+'/',headers={'Host':'attacker.invalid'}).status_code==400
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(channel=args.channel,headless=True)
                context=browser.new_context()
                context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base) else route.abort())
                page=context.new_page();errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                pages=['/','/settings','/aircraft']+['/events/'+identifier for identifier in ids.values()]
                for width in (360,390,430,768,1280):
                    page.set_viewport_size({'width':width,'height':900})
                    for endpoint in pages:
                        response=page.goto(base+endpoint,wait_until='networkidle');assert response.status==200
                        if endpoint=='/aircraft':page.wait_for_selector('#aircraft-list button')
                        if endpoint=='/':page.locator('#source-health').evaluate('(node)=>node.open=true')
                        dimensions=page.evaluate('({client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})')
                        assert dimensions['scroll']<=dimensions['client']+1,(width,endpoint,dimensions)
                        report['viewports'].append({'width':width,'path':endpoint,'overflow':False})
                        if width==390 and endpoint in ('/','/settings','/aircraft','/events/'+ids['sky_crystals'],'/events/'+ids['jupiter']):
                            page.screenshot(path=str(output/(endpoint.strip('/').replace('/','-') or 'home'))+'.png',full_page=True)
                page.set_viewport_size({'width':390,'height':900})
                page.goto(base+'/',wait_until='networkidle')
                assert page.locator('#prediction-sort').input_value()=='time_desc'
                page.locator('[data-selection=none]').click();page.locator('button[type=submit]').click()
                page.wait_for_url(re.compile(r'\?filter=1(?:&|$)'));page.reload();assert not page.locator('[name=types]:checked').count()
                page.locator('[name=types][value=planetary]').check();page.locator('[name=types][value=sky_crystals]').check()
                page.locator('button[type=submit]').click();assert 'types=planetary' in page.url and 'types=sky_crystals' in page.url
                page.locator('#prediction-sort').select_option('interest_desc')
                page.locator('button[type=submit]').click();page.reload()
                assert page.locator('#prediction-sort').input_value()=='interest_desc' and 'sort=interest_desc' in page.url
                assert page.locator('[name=types]:checked').count()==2
                report['sort_selection_and_filter_persistence']=True
                report['filter_refresh_and_multi_select']=True
                page.goto(base+'/aircraft',wait_until='networkidle');page.wait_for_selector('#aircraft-list button')
                page.locator('#aircraft-list button').first.click()
                assert '<img src=x onerror=evil>' in page.locator('#aircraft-detail').inner_text()
                assert page.locator('#aircraft-detail img').count()==0
                page.locator('.leaflet-control-zoom-in').click();page.locator('#map-center').click()
                page.locator('#map-projections').uncheck();report['map_interaction_and_xss']=True
                page.goto(base+'/settings',wait_until='networkidle')
                original=path.read_bytes();page.locator('[name="map.radius_km"]').fill('45')
                page.locator('[name="gmail.immediate.minimum_interest"]').fill('87')
                page.locator('[name="gmail.daily_digest.time"]').fill('08:15')
                page.locator('[name="gmail.weekly_digest.weekday"]').select_option('Saturday')
                immediate=page.locator('[data-types-field="gmail.immediate.event_types"]')
                immediate.locator('[data-all]').uncheck()
                immediate.locator('[data-event-type][value=planetary]').uncheck()
                page.locator('[name="gmail.enabled"]').uncheck();page.locator('button[type=submit]').click()
                expect(page.locator('#settings-result')).to_contain_text('restart required')
                assert load(path).values['map']['radius_km']==45 and path.read_bytes()!=original
                assert service.config.values['map']['radius_km']==40
                saved_mail=load(path).values['gmail']
                assert saved_mail['immediate']['minimum_interest']==87
                assert 'planetary' not in saved_mail['immediate']['event_types']
                assert saved_mail['daily_digest']['event_types']==['*']
                assert saved_mail['daily_digest']['time']=='08:15' and saved_mail['weekly_digest']['weekday']=='Saturday'
                page.reload();assert 'Saved settings differ' in page.inner_text('body')
                report['atomic_settings_save_and_restart_banner']=True
                assert not errors,errors
                report['browser_errors']=errors;report['browser']=browser.version
                context.close();browser.close()
            (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps({key:value for key,value in report.items() if key!='viewports'},indent=2))
            print('Responsive page checks:',len(report['viewports']))
        finally:
            server.should_exit=True;thread.join(10);service.close()
            assert not thread.is_alive()


if __name__=='__main__':main()
