"""Installed-package smoke with private TOML/SQLite; no live config, mail or feeds.

Run with the active venv Python. --camera explicitly opens real hardware and
exercises preview/focus/stills/timelapse. Stop other camera consumers first.
"""
import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.error import URLError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

import tomlkit

import all_sky_alert
from all_sky_alert.config import DEFAULTS
from all_sky_alert.domain import Observer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--camera', action='store_true', help='Explicit real hardware test; fails when unavailable')
    parser.add_argument('--require-installed', action='store_true', help='Reject source/editable imports')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    imported = Path(all_sky_alert.__file__).resolve()
    if args.require_installed and imported.is_relative_to(root / 'src'):
        parser.error('Use a non-editable requirements.txt installation for this check')
    output = root / 'test-artifacts/pi-native-smoke'
    output.mkdir(parents=True, exist_ok=True)
    report = {'host_platform': sys.platform, 'package': str(imported), 'python': sys.version,
              'hardware_requested': args.camera, 'private_storage': True, 'upstream_and_mail': False}
    environment = dict(os.environ)
    environment.pop('PYTHONPATH', None)
    environment.pop('PYTHONHOME', None)
    environment['PYTHONUNBUFFERED'] = '1'
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
    with TemporaryDirectory(prefix='native-', dir=output) as private:
        directory = Path(private)
        values = deepcopy(DEFAULTS)
        for section in values['detectors'].values(): section['enabled'] = False
        for section in values['providers'].values(): section['enabled'] = False
        values['atmosphere']['enabled'] = values['atmosphere']['canadian']['enabled'] = False
        values['notifications']['enabled'] = values['gmail']['enabled'] = False
        values['map']['tiles_enabled'] = False
        values['camera']['enabled'] = args.camera
        path = directory / 'smoke.toml'
        path.write_text(tomlkit.dumps({'observer': asdict(Observer(0, 0, 0, 'UTC')), **values}), encoding='utf-8')
        checked = subprocess.run([sys.executable, '-m', 'all_sky_alert', '--config', str(path), 'check'],
            cwd=directory, env=environment, text=True, capture_output=True, timeout=30, check=True)
        report['configuration_and_sqlite_write'] = 'SQLite writable' in checked.stdout
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        def read(endpoint):
            with opener.open(base + endpoint, timeout=10) as response:
                return response.read()
        def status(): return json.loads(read('/camera/status'))
        def wait_for(check, timeout=30):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if check(): return
                time.sleep(.1)
            raise AssertionError('Smoke deadline exceeded')
        log_path = output / 'server.log'
        stream = None
        with log_path.open('w', encoding='utf-8') as log:
            process = subprocess.Popen([sys.executable, '-m', 'all_sky_alert', '--config', str(path),
                'serve', '--host', '127.0.0.1', '--port', str(port)],
                cwd=directory, env=environment, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
            try:
                def ready():
                    if process.poll() is not None: raise AssertionError('Server exited; inspect server.log')
                    try: return bool(read('/api/health'))
                    except URLError: return False
                wait_for(ready)
                for endpoint in ('/', '/camera', '/settings', '/static/style.css', '/static/camera.js'):
                    assert read(endpoint), endpoint
                health = json.loads(read('/api/health'))
                wait_for(lambda: any(row['name'] == 'worker:slow' and row['status'] == 'ok'
                    for row in json.loads(read('/api/health'))['components']))
                report['http_routes_and_worker'] = True
                report['sources'] = health['sources']
                if args.camera:
                    wait_for(lambda: status()['state'] != 'starting')
                    camera = status()
                    assert camera['state'] == 'ready', camera['error']
                    page = read('/camera').decode('utf-8')
                    csrf = re.search(r'data-csrf="([^"]+)"', page)[1]
                    def command(payload):
                        request = Request(base + '/camera/command', data=json.dumps(payload).encode(),
                            headers={'Content-Type': 'application/json', 'Origin': base, 'X-CSRF-Token': csrf})
                        with opener.open(request, timeout=10) as response: result = json.load(response)
                        if 'id' in result:
                            key = result['id']
                            wait_for(lambda: next(job for job in status()['jobs'] if job['id'] == key)['status'] not in {'queued', 'running'})
                            job = next(job for job in status()['jobs'] if job['id'] == key)
                            assert job['status'] == 'done', job
                        return result
                    stream = opener.open(base + '/camera/stream', timeout=20)
                    assert stream.read(128).startswith(b'--frame')
                    stream.close(); stream = None
                    if camera['capabilities']['autofocus_trigger']:
                        command({'action': 'focus', 'mode': 'Auto', 'trigger': True})
                        wait_for(lambda: status()['autofocus_state'] in (2, 3), timeout=30)
                        report['autofocus_state'] = status()['autofocus_state']
                    command({'action': 'capture'})
                    captured = status()
                    from PIL import Image
                    with Image.open(Path(captured['capture_directory']) / captured['last_filename']) as image:
                        assert list(image.size) == captured['sensor_resolution']
                        report['still_resolution'] = list(image.size)
                    command({'action': 'timelapse', 'running': True, 'interval_seconds': 10})
                    captures = {}
                    def three_images():
                        current = status()
                        if current['last_filename'] and current['last_filename'].startswith('timelapse-'):
                            captures[current['last_filename']] = current['last_capture_at']
                        return len(captures) >= 3
                    wait_for(three_images, timeout=90)
                    command({'action': 'timelapse', 'running': False, 'interval_seconds': 10})
                    stamps = sorted(datetime.fromisoformat(value) for value in captures.values())
                    assert all((b - a).total_seconds() >= 10 for a, b in zip(stamps, stamps[1:]))
                    report['timelapse_captures'] = captures
                    report['camera_model'] = captured['model']
                    # Keep a real preview connected while testing POSIX shutdown.
                    stream = opener.open(base + '/camera/stream', timeout=20)
                    assert stream.read(128).startswith(b'--frame')
                if os.name == 'posix':
                    process.send_signal(signal.SIGTERM)
                    process.wait(timeout=50)
                    assert process.returncode in (0, -signal.SIGTERM), process.returncode
                    report['sigterm'] = 'passed'
                else:
                    report['sigterm'] = 'skipped: POSIX SIGTERM unavailable; Windows Ctrl+Break tested instead'
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                    process.wait(timeout=10)
                if args.camera and os.name == 'posix':
                    from picamera2 import Picamera2
                    reopened = Picamera2()
                    reopened.close()
                    report['camera_reopened_after_shutdown'] = True
            finally:
                if stream is not None: stream.close()
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
        assert 'Application shutdown complete' in log_path.read_text(encoding='utf-8')
        report['lifespan_shutdown'] = 'passed'
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
