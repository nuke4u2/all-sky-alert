from copy import deepcopy
from datetime import timedelta
from enum import IntEnum
import re
from threading import Event, get_ident
import time
import tomllib
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from all_sky_alert.camera.backend import RaspberryPiCameraBackend, CameraFault
from all_sky_alert.camera.manager import CameraManager, CameraUnavailable, CameraBusy
from all_sky_alert.camera.settings import validate_interval
from all_sky_alert.camera.timelapse import TimelapseSchedule, capture_name
from all_sky_alert.config import from_dict, load
from all_sky_alert.service import Service
from all_sky_alert.web import create_app
from conftest import FakeConditions


def wait_for(check):
    deadline = time.monotonic() + 5
    while not check():
        if time.monotonic() >= deadline:
            raise AssertionError('Camera worker did not reach expected state')
        time.sleep(.01)


class FakeBackend:
    def __init__(self):
        self.calls, self.paths = [], []
        self.closed = False
        self.fail_capture = False
        self.fail_open = False
        self.fail_focus = False
        self.entered, self.release = Event(), Event()
        self.block = False

    def call(self, name):
        self.calls.append((name, get_ident()))

    def open(self, settings):
        self.call('open')
        if self.fail_open:
            raise OSError('secret upstream error must not appear')
        return {'model': 'imx708', 'sensor_resolution': [4608, 2592], 'focus_mode': 'Continuous',
                'capabilities': {'focus_modes': ['Auto', 'Continuous', 'Manual'],
                                 'autofocus_trigger': True, 'lens_limits': [0, 7, 1]}}

    def metadata(self):
        self.call('metadata')
        return {'LensPosition': 1.25, 'AfState': 2}

    def preview(self):
        self.call('preview')
        return b'\xff\xd8test\xff\xd9', self.metadata()

    def capture(self, path):
        self.call('capture')
        self.paths.append(path)
        self.entered.set()
        if self.block:
            assert self.release.wait(5)
        path.write_bytes(b'partial' if self.fail_capture else b'jpeg-fixture')
        if self.fail_capture:
            raise OSError('private driver details')
        return self.metadata()

    def focus(self, **values):
        self.call('focus')
        if self.fail_focus:
            raise RuntimeError('private autofocus failure')

    def close(self):
        self.call('close')
        self.closed = True


@pytest.mark.parametrize('value', [0, -1, 9, True, '60', float('nan'), float('inf'), 604801])
def test_interval_rejects_invalid(value):
    with pytest.raises(ValueError):
        validate_interval(value)


def test_schedule_no_catchup_completion_pacing_and_stop():
    schedule = TimelapseSchedule(10)
    schedule.configure(True, 10, 100)
    assert not schedule.due(109.9) and schedule.due(110)
    schedule.completed(150)
    assert not schedule.due(159) and schedule.due(160)
    schedule.configure(True, 60, 170)
    assert schedule.deadline == 230
    schedule.configure(False, 60, 180)
    assert schedule.deadline is None and not schedule.due(10000)


def test_capture_filenames_utc_unique_and_no_separators(now):
    name = capture_name(now, 'timelapse')
    assert re.fullmatch(r'timelapse-20261006T180000\.000000Z-[a-f0-9]{12}\.jpg', name)
    assert name != capture_name(now, 'timelapse')
    with pytest.raises(ValueError):
        capture_name(now.replace(tzinfo=None), 'still')
    with pytest.raises(ValueError):
        capture_name(now, '../other')


@pytest.mark.parametrize('patch', [{'enabled': 'true'}, {'backend': 'zwo'}, {'camera_index': True},
    {'preview_width': 640.5}, {'preview_fps': 20}, {'capture_directory': '../src'},
    {'timelapse': {'interval_seconds': 0}}, {'timelapse': {'enabled': 1}}, {'typo': True}])
def test_camera_config_rejects_invalid(tmp_path, patch):
    raw = {'observer': {'latitude': 0, 'longitude': 0, 'timezone': 'UTC'}, 'camera': patch}
    with pytest.raises(ValueError):
        from_dict(raw, tmp_path)


def test_old_config_disabled_and_example_loads(tmp_path):
    cfg = from_dict({'observer': {'latitude': 0, 'longitude': 0, 'timezone': 'UTC'},
                     'service': {'data_dir': 'private-runtime'}}, tmp_path)
    assert not cfg.values['camera']['enabled']
    assert CameraManager(cfg).capture_directory == tmp_path / 'private-runtime/camera/stills'
    assert not load('config.example.toml').values['camera']['enabled']


def test_manager_single_owner_capture_preview_focus_failure_shutdown(config, now):
    config.values['camera']['enabled'] = True
    backend = FakeBackend()
    manager = CameraManager(config, backend_factory=lambda: backend, clock=lambda: now)
    assert not backend.calls
    manager.start(); manager.start()
    try:
        wait_for(lambda: manager.snapshot()['state'] == 'ready')
        assert not any(name == 'preview' for name, _ in backend.calls)
        manager.subscribe()
        wait_for(lambda: manager.preview_frame(-1)[1] is not None)
        sequence, frame = manager.preview_frame(-1)
        assert frame.startswith(b'\xff\xd8') and manager.preview_frame(sequence)[1] is None
        manager.unsubscribe()
        assert manager.preview_frame(-1)[1] is None
        with pytest.raises(ValueError): manager.submit('focus', mode='Manual', lens_position=8)
        with pytest.raises(ValueError): manager.submit('focus', mode='Manual', lens_position=True)
        backend.fail_focus = True
        key = manager.submit('focus', mode='Auto', trigger=True, lens_position=None)
        wait_for(lambda: manager.jobs[key]['status'] == 'failed')
        backend.fail_focus = False
        key = manager.submit('focus', mode='Manual', trigger=False, lens_position=2)
        wait_for(lambda: manager.jobs[key]['status'] == 'done')
        assert manager.snapshot()['focus_mode'] == 'Manual'
        backend.fail_capture = True
        key = manager.submit('capture')
        wait_for(lambda: manager.jobs[key]['status'] == 'failed')
        assert 'private' not in manager.snapshot()['error']
        assert not list(manager.capture_directory.glob('*.part'))
        backend.fail_capture = False
        key = manager.submit('capture')
        wait_for(lambda: manager.jobs[key]['status'] == 'done')
        assert manager.latest_path.read_bytes() == b'jpeg-fixture'
        assert manager.snapshot()['last_capture_at'] == now.isoformat()
        assert manager.snapshot()['error'] is None
    finally:
        manager.close(); manager.close()
    assert backend.closed and not manager.thread.is_alive()
    assert {identity for _, identity in backend.calls} == {manager.thread.ident}
    assert manager.snapshot()['state'] == 'stopped'
    with pytest.raises(CameraUnavailable): manager.submit('capture')


def test_unavailable_camera_isolated_and_released(config):
    config.values['camera']['enabled'] = True
    backend = FakeBackend(); backend.fail_open = True
    manager = CameraManager(config, backend_factory=lambda: backend)
    manager.start()
    try:
        wait_for(lambda: manager.snapshot()['state'] == 'error')
        wait_for(lambda: backend.closed)
        assert manager.snapshot()['detected'] is False
        assert 'secret' not in manager.snapshot()['error']
        with pytest.raises(CameraUnavailable): manager.subscribe()
    finally: manager.close()


def test_timelapse_failure_interval_stop_and_no_overlapping_capture(config, now):
    config.values['camera']['enabled'] = True
    elapsed = [100.0]
    backend = FakeBackend()
    manager = CameraManager(config, backend_factory=lambda: backend,
                            clock=lambda: now + timedelta(seconds=elapsed[0]), monotonic=lambda: elapsed[0])
    manager.start()
    try:
        wait_for(lambda: manager.snapshot()['state'] == 'ready')
        manager.set_timelapse(True, 10)
        assert manager.snapshot()['next_capture_at'] == (now + timedelta(seconds=110)).isoformat()
        backend.fail_capture = True
        elapsed[0] = 110; manager.wake.set()
        wait_for(lambda: manager.snapshot()['error'] is not None)
        wait_for(lambda: manager.schedule.deadline == 120)
        backend.fail_capture = False
        elapsed[0] = 119; manager.wake.set()
        assert len(backend.paths) == 1
        backend.block = True; backend.entered.clear()
        elapsed[0] = 120; manager.wake.set()
        assert backend.entered.wait(5)
        key = manager.submit('capture')
        with pytest.raises(CameraBusy): manager.submit('capture')
        assert manager.snapshot()['state'] == 'capturing'
        manager.set_timelapse(False, 10)
        backend.release.set()
        wait_for(lambda: manager.jobs[key]['status'] == 'done')
        assert len(backend.paths) == 3
        elapsed[0] = 1000; manager.wake.set()
        assert not manager.schedule.due(elapsed[0])
        assert manager.snapshot()['next_capture_at'] is None
    finally:
        backend.release.set(); manager.close()


def test_preview_viewer_bound_and_disabled(config):
    config.values['camera']['enabled'] = True
    backend = FakeBackend()
    manager = CameraManager(config, backend_factory=lambda: backend)
    manager.start()
    try:
        wait_for(lambda: manager.snapshot()['state'] == 'ready')
        for _ in range(4): manager.subscribe()
        with pytest.raises(CameraBusy): manager.subscribe()
        for _ in range(4): manager.unsubscribe()
        manager.settings['preview_enabled'] = False
        with pytest.raises(CameraUnavailable): manager.subscribe()
    finally: manager.close()


class AfMode(IntEnum):
    Manual = 0
    Auto = 1
    Continuous = 2


class MockPicamera:
    def __init__(self, index):
        self.sensor_resolution = (4608, 2592)
        self.camera_properties = {'Model': 'imx708'}
        self.camera_controls = {'AfMode': (0, 2, 0), 'AfTrigger': (0, 1, 0), 'LensPosition': (.25, 8, 1)}
        self.calls, self.config, self.closed, self.fail = [], None, False, False
    def create_preview_configuration(self, **kwargs): return {'type': 'preview', **kwargs}
    def create_still_configuration(self, **kwargs): return {'type': 'still', **kwargs}
    def configure(self, config): self.config = config; self.calls.append(('configure', config))
    def set_controls(self, values): self.calls.append(('controls', values))
    def start(self, **kwargs): self.calls.append(('start', kwargs))
    def stop(self): self.calls.append(('stop', None))
    def close(self): self.closed = True
    def capture_file(self, output, **kwargs):
        assert kwargs == {'format': 'jpeg', 'wait': False}
        self.calls.append(('capture', self.config['main']['size']))
        if self.fail: raise OSError('disk full')
        if hasattr(output, 'write'): output.write(b'jpeg')
        else:
            from pathlib import Path
            Path(output).write_bytes(b'jpeg')
        return {'LensPosition': 2.0, 'AfState': 2}
    def capture_metadata(self, **kwargs): return {'LensPosition': 2.0, 'AfState': 2}
    def wait(self, job, timeout): assert timeout == 15; return job


def test_picamera_backend_full_resolution_restoration_supported_limits(config, tmp_path):
    camera = MockPicamera(0)
    controls = SimpleNamespace(AfModeEnum=AfMode, AfTriggerEnum=SimpleNamespace(Start=0))
    backend = RaspberryPiCameraBackend(camera_factory=lambda _: camera, controls=controls)
    try:
        info = backend.open(config.values['camera'])
        assert info['capabilities']['lens_limits'] == [.25, 8, 1]
        backend.focus('Manual', 3)
        backend.capture(tmp_path / 'capture.jpg')
        assert ('capture', (4608, 2592)) in camera.calls
        assert camera.config['type'] == 'preview'
        assert camera.calls[-2] == ('controls', {'AfMode': AfMode.Manual, 'LensPosition': 3})
        camera.fail = True
        with pytest.raises(OSError): backend.capture(tmp_path / 'failed.jpg')
        assert camera.config['type'] == 'preview'
        camera.fail = False
        assert backend.preview()[0] == b'jpeg'
        with pytest.raises(ValueError): backend.focus('Manual', 0)
        with pytest.raises(ValueError): backend.focus('Continuous', trigger=True)
    finally: backend.close()
    assert camera.closed


@pytest.fixture
def camera_config(tmp_path):
    path = tmp_path / 'config.toml'
    path.write_text('[observer]\nlatitude=0\nlongitude=0\ntimezone="UTC"\n[camera]\nenabled=true\n', encoding='utf-8')
    return load(path)


def test_camera_web_routes_security_persistence_and_no_acquisition(camera_config, conditions):
    backend = FakeBackend()
    manager = CameraManager(camera_config, backend_factory=lambda: backend)
    service = Service(camera_config, camera=manager, conditions=FakeConditions(conditions), detectors=[], sources={}, adapters=[])
    try:
        with TestClient(create_app(service, background=False), base_url='http://localhost') as client:
            page = client.get('/camera')
            assert page.status_code == 200 and 'General Pi Camera / Timelapse' in page.text
            assert client.get('/camera/status').json()['state'] == 'stopped'
            assert not backend.calls
            assert client.get('/camera/stream').status_code == 503
            assert client.get('/camera/latest').status_code == 404
            csrf = re.search(r'data-csrf="([^"]+)"', page.text)[1]
            revision = re.search(r'data-revision="([^"]+)"', page.text)[1]
            headers = {'Origin': 'http://localhost', 'X-CSRF-Token': csrf}
            assert client.post('/camera/command', json={'action': 'capture'}).status_code == 403
            assert client.post('/camera/command', json={'action': 'capture'}, headers={**headers, 'Origin': 'http://evil.example'}).status_code == 403
            assert client.post('/camera/command', content=b'x' * 33000, headers={**headers, 'Content-Type': 'application/json'}).status_code == 413
            assert client.post('/camera/command', json={'action': 'capture', 'path': '../../x'}, headers=headers).status_code == 422
            manager.start(); wait_for(lambda: manager.snapshot()['state'] == 'ready')
            assert client.post('/camera/command', json={'action': 'timelapse', 'running': True, 'interval_seconds': 0}, headers=headers).status_code == 422
            response = client.post('/camera/command', json={'action': 'timelapse', 'running': True, 'interval_seconds': 60}, headers=headers)
            assert response.status_code == 200 and manager.snapshot()['timelapse_running']
            assert not load(camera_config.file_path).values['camera']['timelapse']['enabled']
            response = client.post('/camera/command', json={'action': 'capture'}, headers=headers)
            assert response.status_code == 202
            key = response.json()['id']; wait_for(lambda: manager.jobs[key]['status'] == 'done')
            assert client.get('/camera/latest').content == b'jpeg-fixture'
            body = {'action': 'save_timelapse', 'enabled': True, 'interval_seconds': 120, 'revision': revision}
            assert client.post('/camera/command', json=body, headers=headers).json()['restart_required']
            assert load(camera_config.file_path).values['camera']['timelapse'] == {'enabled': True, 'interval_seconds': 120, 'directory': 'camera/timelapse'}
            assert manager.snapshot()['interval_seconds'] == 60
            assert client.post('/camera/command', json=body, headers=headers).status_code == 409
            assert client.post('/camera/command', json={'action': 'timelapse', 'running': False, 'interval_seconds': 60}, headers=headers).status_code == 200
            assert not manager.snapshot()['timelapse_running']
            assert client.get('/api/health').json()['camera']['detected']
    finally: service.close()
    assert backend.closed


def test_camera_lifespan_starts_releases_and_workers_continue(camera_config, conditions):
    backend = FakeBackend(); backend.fail_open = True
    manager = CameraManager(camera_config, backend_factory=lambda: backend)
    service = Service(camera_config, camera=manager, conditions=FakeConditions(conditions), detectors=[], sources={}, adapters=[])
    with TestClient(create_app(service), base_url='http://localhost') as client:
        wait_for(lambda: manager.snapshot()['state'] == 'error')
        assert client.get('/').status_code == 200
        wait_for(lambda: any(row['name'] == 'worker:slow' for row in service.store.health_rows()))
        assert client.get('/camera/status').json()['state'] == 'error'
    assert backend.closed and not manager.thread.is_alive()


def test_runtime_requirements_follow_project_metadata():
    from pathlib import Path
    requirements = [line for line in Path('requirements.txt').read_text().splitlines() if line and not line.startswith('#')]
    assert requirements == ['-c constraints.txt', '.']
    project = tomllib.loads(Path('pyproject.toml').read_text())['project']
    assert not any('picamera' in package.lower() for package in project['dependencies'])


def test_timed_out_picamera_job_flushed_and_not_reused(config, tmp_path):
    camera = MockPicamera(0)
    controls = SimpleNamespace(AfModeEnum=AfMode, AfTriggerEnum=SimpleNamespace(Start=0))
    backend = RaspberryPiCameraBackend(camera_factory=lambda _: camera, controls=controls)
    backend.open(config.values['camera'])
    flushed = []
    camera.cancel_all_and_flush = lambda: flushed.append(True)
    def timeout(job, timeout): raise TimeoutError('sensor disconnected')
    camera.wait = timeout
    try:
        with pytest.raises(CameraFault): backend.capture(tmp_path / 'timeout.jpg')
        assert flushed == [True] and backend.failed
        assert camera.calls[-1] == ('stop', None)
    finally: backend.close()
    assert camera.closed


def test_camera_worker_fault_releases_without_prediction_failure(config):
    config.values['camera']['enabled'] = True
    backend = FakeBackend()
    def timeout(): raise CameraFault('sensor timeout')
    backend.metadata = timeout
    manager = CameraManager(config, backend_factory=lambda: backend)
    manager.start()
    try:
        wait_for(lambda: manager.snapshot()['state'] == 'error')
        wait_for(lambda: backend.closed)
        assert manager.snapshot()['detected'] is True
        assert not manager.snapshot()['timelapse_running']
        with pytest.raises(CameraUnavailable): manager.submit('capture')
    finally: manager.close()


def test_check_cli_offline_database_write_no_camera_or_acquisition(camera_config, monkeypatch, capsys):
    from all_sky_alert.cli import main
    from all_sky_alert.storage import Store
    def forbidden(*args, **kwargs): raise AssertionError('check must not acquire or open hardware')
    monkeypatch.setattr('all_sky_alert.camera.manager.CameraManager.start', forbidden)
    monkeypatch.setattr('all_sky_alert.service.Service.tick', forbidden)
    monkeypatch.setattr('sys.argv', ['all-sky-alert', '--config', str(camera_config.file_path), 'check'])
    main()
    assert 'SQLite writable' in capsys.readouterr().out
    store = Store(camera_config.data_dir / 'all-sky-alert.sqlite3')
    assert any(row['name'] == 'startup-check' and row['status'] == 'ok' for row in store.health_rows())


def test_unsupported_focus_controls_backend(config):
    camera = MockPicamera(0)
    camera.camera_controls = {}
    backend = RaspberryPiCameraBackend(camera_factory=lambda _: camera, controls=SimpleNamespace())
    try:
        info = backend.open(config.values['camera'])
        assert info['capabilities'] == {'focus_modes': [], 'autofocus_trigger': False, 'lens_limits': None}
        with pytest.raises(ValueError): backend.focus('Auto')
    finally: backend.close()
