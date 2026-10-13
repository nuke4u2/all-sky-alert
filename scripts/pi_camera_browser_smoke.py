"""Offline fake-camera web QA. No production config, providers, devices or mail."""
from argparse import ArgumentParser
import base64
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import time

from playwright.sync_api import expect, sync_playwright
import tomlkit
import uvicorn

from all_sky_alert.camera.manager import CameraManager
from all_sky_alert.config import DEFAULTS, load
from all_sky_alert.domain import Conditions, Observer
from all_sky_alert.service import Service
from all_sky_alert.server import ApplicationServer
from all_sky_alert.web import create_app


class FixtureCamera:
    def __init__(self, jpeg):
        self.jpeg = jpeg
        self.closed = False
        self.frames = 0

    def open(self, settings):
        return {'model': 'imx708 fixture <img src=x onerror=evil>', 'sensor_resolution': [4608, 2592],
                'focus_mode': 'Continuous', 'capabilities': {'focus_modes': ['Auto', 'Continuous', 'Manual'],
                'autofocus_trigger': True, 'lens_limits': [.25, 8, 1]}}
    def metadata(self): return {'LensPosition': 1, 'AfState': 2}
    def preview(self): self.frames += 1; return self.jpeg, self.metadata()
    def capture(self, path): path.write_bytes(self.jpeg); return self.metadata()
    def focus(self, **values): pass
    def close(self): self.closed = True


class FixtureConditions:
    def refresh(self, now): pass
    def at(self, when, geometry): return Conditions({}, 'unknown', None, None)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--channel', default='msedge' if os.name == 'nt' else None,
                        help='Installed browser channel; default Edge on Windows, Playwright Chromium elsewhere')
    parser.add_argument('--port', type=int, default=8023)
    args = parser.parse_args()
    output = Path('test-artifacts/pi-camera-browser')
    output.mkdir(parents=True, exist_ok=True)
    report = {'fixture_only': True, 'hardware': False, 'viewports': []}
    # Browser canvas creates the synthetic JPEG fixture; no imaging dependency.
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=args.channel, headless=True)
        page = browser.new_page()
        encoded = page.evaluate('''() => {
            const canvas = document.createElement('canvas');
            canvas.width = 640; canvas.height = 480;
            const context = canvas.getContext('2d');
            context.fillStyle = '#2d465a'; context.fillRect(0, 0, 640, 480);
            return canvas.toDataURL('image/jpeg').split(',')[1];
        }''')
        browser.close()
    jpeg = base64.b64decode(encoded)
    with TemporaryDirectory(prefix='sky-camera-qa-') as private:
        path = Path(private) / 'config.toml'
        values = deepcopy(DEFAULTS)
        values['camera']['enabled'] = True
        values['map']['tiles_enabled'] = False
        for settings in values['detectors'].values(): settings['enabled'] = False
        path.write_text(tomlkit.dumps({'observer': asdict(Observer(0, 0, 0, 'UTC')), **values}), encoding='utf-8')
        config = load(path)
        backend = FixtureCamera(jpeg)
        camera = CameraManager(config, backend_factory=lambda: backend)
        service = Service(config, camera=camera, conditions=FixtureConditions(), sources={}, detectors=[], adapters=[])
        server = ApplicationServer(uvicorn.Config(create_app(service), host='127.0.0.1', port=args.port,
            log_level='warning', proxy_headers=False, timeout_graceful_shutdown=2), camera)
        thread = Thread(target=server.run, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{args.port}'
        try:
            for _ in range(100):
                if server.started and camera.snapshot()['state'] == 'ready': break
                time.sleep(.05)
            assert server.started
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel=args.channel, headless=True)
                context = browser.new_context(accept_downloads=True)
                context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                for width in (360, 390, 768, 1280):
                    page.set_viewport_size({'width': width, 'height': 900})
                    assert page.goto(base + '/camera', wait_until='networkidle').status == 200
                    expect(page.locator('#camera-status')).to_contain_text('imx708 fixture')
                    assert page.locator('#camera-status img').count() == 0
                    dimensions = page.evaluate('({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})')
                    assert dimensions['scroll'] <= dimensions['width'] + 1, dimensions
                    page.screenshot(path=str(output / f'camera-{width}.png'), full_page=True)
                    report['viewports'].append({'width': width, 'overflow': False})
                page.set_viewport_size({'width': 390, 'height': 900})
                page.locator('#preview-start').click()
                expect(page.locator('#camera-preview')).to_be_visible()
                page.wait_for_function('() => document.getElementById("camera-preview").naturalWidth > 0')
                page.screenshot(path=str(output / 'camera-live-390.png'), full_page=True)
                assert backend.frames > 0
                page.locator('#preview-stop').click()
                expect(page.locator('#camera-preview')).to_be_hidden()
                for _ in range(100):
                    if camera.viewers == 0: break
                    time.sleep(.02)
                assert camera.viewers == 0
                frames = backend.frames
                time.sleep(.7)
                assert backend.frames == frames
                page.locator('#autofocus').click()
                expect(page.locator('#camera-result')).to_contain_text('Focus controls applied')
                page.locator('#focus-mode').select_option('Manual')
                page.locator('#lens-position').fill('3')
                page.locator('#focus-apply').click()
                expect(page.locator('#camera-result')).to_contain_text('Focus controls applied')
                page.locator('#capture').click()
                expect(page.locator('#camera-result')).to_contain_text('Image captured:')
                with page.expect_download() as download:
                    page.locator('#latest-image').click()
                assert download.value.suggested_filename.startswith('still-')
                page.locator('#capture-interval').fill('1')
                page.locator('#timelapse-start').click()
                expect(page.locator('#camera-result')).to_contain_text('between 10 seconds')
                page.locator('#interval-unit').select_option('60')
                page.locator('#timelapse-start').click()
                expect(page.locator('#camera-result')).to_contain_text('running state updated')
                assert camera.snapshot()['interval_seconds'] == 60 and camera.snapshot()['timelapse_running']
                page.locator('#timelapse-stop').click()
                for _ in range(100):
                    if not camera.snapshot()['timelapse_running']: break
                    time.sleep(.02)
                assert not camera.snapshot()['timelapse_running']
                page.locator('#startup-timelapse').check()
                page.locator('#timelapse-save').click()
                expect(page.locator('#camera-result')).to_contain_text('restart required')
                assert load(path).values['camera']['timelapse']['enabled']
                assert page.goto(base + '/settings', wait_until='networkidle').status == 200
                expect(page.locator('[name="camera.enabled"]')).to_be_checked()
                expect(page.locator('[name="camera.timelapse.enabled"]')).to_be_checked()
                assert not errors, errors
                report.update(browser=browser.version, preview_and_disconnect=True, controls=True,
                              capture_download=True, timelapse_session_and_save=True, browser_errors=errors)
                page.goto(base + '/camera', wait_until='networkidle')
                page.locator('#preview-start').click()
                page.wait_for_function('() => document.getElementById("camera-preview").naturalWidth > 0')
                assert camera.viewers == 1
                # Infinite HTTP streams must not prevent lifespan shutdown/release.
                server.should_exit = True
                thread.join(8)
                assert not thread.is_alive() and backend.closed
                report['shutdown_with_open_stream'] = True
                context.close()
                browser.close()
        finally:
            server.should_exit = True
            thread.join(15)
            service.close()
            assert not thread.is_alive() and backend.closed and not camera.thread.is_alive()
        report['owner_released'] = True
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
