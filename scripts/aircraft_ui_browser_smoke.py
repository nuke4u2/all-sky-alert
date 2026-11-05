"""Fixture-only aircraft UI QA; no production config/data, upstream feeds or mail.

Requires the optional qa extra and an installed browser. Outputs screenshots and
a report under test-artifacts/aircraft-ui-browser. All displayed flights are synthetic.
"""
from argparse import ArgumentParser
from copy import deepcopy
from datetime import timedelta
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import time

import httpx
from playwright.sync_api import expect, sync_playwright
import uvicorn

from all_sky_alert.adsb import ADSBLol
from all_sky_alert.aircraft import AircraftDetector
from all_sky_alert.config import Config, DEFAULTS
from all_sky_alert.domain import Conditions, Observer, iso, parse
from all_sky_alert.enrichment import LocalEnrichment
from all_sky_alert.service import Service
from all_sky_alert.storage import Store
from all_sky_alert.web import create_app


class NoWeather:
    def refresh(self, now):
        pass

    def at(self, when, geometry):
        return Conditions({}, 'disabled', None, None)


def main():
    parser=ArgumentParser()
    parser.add_argument('--channel',default='msedge' if os.name=='nt' else None)
    parser.add_argument('--port',type=int,default=8023)
    args=parser.parse_args()
    output=Path('test-artifacts/aircraft-ui-browser')
    output.mkdir(parents=True,exist_ok=True)
    payload=json.loads(Path('tests/fixtures/adsb-radius.json').read_text())
    payload['ac']=payload['ac'][:2]
    payload['ac'][1]['t']='B738'
    clock=[parse('2026-11-04T18:00:00Z')]
    payload['now']=clock[0].timestamp()*1000
    calls=[]
    def upstream(request):
        assert request.url.host=='api.adsb.lol'
        calls.append(request.url.host)
        return httpx.Response(200,json=payload)
    with TemporaryDirectory(prefix='aircraft-ui-') as private:
        base_path=Path(private)
        values=deepcopy(DEFAULTS)
        values['service']['data_dir']='fixture-data'
        values['detectors']['adsb']['enabled']=True
        values['map']['tiles_enabled']=False
        config=Config(Observer(51.4779,0,46,'Europe/London'),values,base_path)
        metadata_path=base_path/'metadata.json'
        metadata_path.write_text(json.dumps([{
            'icao24':'abc123','callsign':'JBU123','airline':'Example Air',
            'manufacturer':'Airbus','base_model':'A320','origin':'JFK','destination':'BOS',
            'updated_at':iso(clock[0]-timedelta(hours=1)),
            'valid_until':iso(clock[0]+timedelta(hours=1)),
            'source_name':'Synthetic QA','source_url':'https://example.org/',
            'rights':'Synthetic fixture owned by this project',
        }]),encoding='utf-8')
        client=httpx.Client(transport=httpx.MockTransport(upstream))
        store=Store(base_path/'fixture.sqlite3')
        source=ADSBLol(client,store,config.observer,deepcopy(ADSBLol.defaults),['adsb'])
        metadata=LocalEnrichment(client,store,config.observer,
            {**LocalEnrichment.defaults,'enabled':True,'path':str(metadata_path)},['adsb'])
        detector=AircraftDetector(deepcopy(AircraftDetector.defaults),source,metadata)
        service=Service(config,store=store,conditions=NoWeather(),detectors=[detector],
            sources={'adsb':source,'enrichment':metadata},adapters=[],clock=lambda:clock[0])
        service.tick(clock[0],lane='live',deliver=False)
        event_id=service.upcoming(clock[0])[0]['candidate']['id']
        base=f'http://127.0.0.1:{args.port}'
        server=uvicorn.Server(uvicorn.Config(create_app(service,background=False),
            host='127.0.0.1',port=args.port,proxy_headers=False,log_level='warning'))
        thread=Thread(target=server.run,daemon=True)
        thread.start()
        try:
            with httpx.Client(timeout=5,trust_env=False) as probe:
                for _ in range(60):
                    try:
                        probe.get(base+'/api/health').raise_for_status()
                        break
                    except httpx.ConnectError:
                        time.sleep(.1)
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(channel=args.channel,headless=True)
                context=browser.new_context()
                external=[]
                def local_only(route):
                    if route.request.url.startswith(base+'/'):
                        route.continue_()
                    else:
                        external.append(route.request.url)
                        route.abort()
                context.route('**/*',local_only)
                page=context.new_page()
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                checks=[]
                for width in (360,390,768,1280):
                    page.set_viewport_size({'width':width,'height':900})
                    for path in ('/','/aircraft','/events/'+event_id):
                        page.goto(base+path,wait_until='networkidle')
                        if path=='/':
                            expect(page.locator('#live-aircraft .aircraft-airline').first).to_have_text('Example Air')
                            expect(page.locator('#live-aircraft .aircraft-type').first).to_have_text('Airbus A320')
                            expect(page.locator('#live-aircraft .aircraft-route').first).to_contain_text('JFK')
                        elif path=='/aircraft':
                            expect(page.locator('#aircraft-list button')).to_have_count(2)
                            chosen=page.locator('#aircraft-list button[data-icao24="abc123"]')
                            expect(chosen).to_contain_text('Example Air')
                            chosen.click()
                            expect(chosen).to_have_attribute('aria-pressed','true')
                            expect(page.locator('#aircraft-detail .aircraft-type')).to_have_text('Airbus A320')
                            expect(page.locator('#aircraft-detail .aircraft-route')).to_contain_text('BOS')
                            expect(page.locator('#aircraft-list')).to_contain_text('Airline unknown')
                            expect(page.locator('#aircraft-list')).to_contain_text('Boeing 737')
                            page.locator('#map-projections').uncheck()
                        else:
                            expect(page.locator('h1')).to_have_text('Airbus A320')
                            expect(page.locator('main > .aircraft-airline')).to_have_text('Example Air')
                        dimensions=page.evaluate('({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})')
                        assert dimensions['scroll']<=dimensions['width']+1,(width,path,dimensions)
                        checks.append({'width':width,'path':path})
                        if width in (390,1280):
                            name='home' if path=='/' else 'map' if path=='/aircraft' else 'detail'
                            page.screenshot(path=str(output/f'{name}-{width}.png'),full_page=True)
                evil='<img src=x onerror=evil>'
                def poisoned(route):
                    response=route.fetch()
                    data=response.json()
                    for row in data['aircraft']:
                        row['aircraft_summary']['airline']=evil
                        row['aircraft_summary']['origin']=evil
                    route.fulfill(response=response,json=data)
                context.route('**/api/aircraft',poisoned)
                context.route('**/api/live?*',poisoned)
                for path in ('/','/aircraft'):
                    page.goto(base+path,wait_until='networkidle')
                    root=page.locator('#live-aircraft' if path=='/' else '#aircraft-list')
                    expect(root).to_contain_text(evil)
                    assert root.locator('img').count()==0
                    if path=='/aircraft':
                        root.locator('button').first.click()
                        expect(page.locator('#aircraft-detail')).to_contain_text(evil)
                        assert page.locator('#aircraft-detail img').count()==0
                clock[0]+=timedelta(seconds=31)
                for path in ('/','/aircraft'):
                    page.goto(base+path,wait_until='networkidle')
                    selector='#live-aircraft article' if path=='/' else '#aircraft-list button'
                    expect(page.locator(selector)).to_have_count(0)
                assert calls==['api.adsb.lol'],calls
                assert not errors and not external,(errors,external)
                report={'fixture_only':True,'checks':checks,'browser':browser.version,
                    'xss_safe':True,'position_expiry':True,'upstream_calls':len(calls),
                    'external_browser_requests':external,'browser_errors':errors}
                (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
                context.close()
                browser.close()
                print(json.dumps(report,indent=2))
        finally:
            server.should_exit=True
            thread.join(10)
            service.close()
            client.close()
            assert not thread.is_alive()


if __name__=='__main__':
    main()
