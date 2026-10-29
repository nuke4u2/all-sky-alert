from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .domain import parse
from .locking import ServiceLock
from .service import Service
from .network import LANMiddleware,ExactHostMiddleware
from .registry import DETECTORS
from .config_edit import ConfigEditor,field_definitions,get_value,runtime_identity
from .health import aggregate
from .tracks import map_snapshot
from .camera.manager import CameraUnavailable, CameraBusy


def safe_url(value: str) -> str:
    try:
        parsed = urlparse(value)
        return value if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username else "#"
    except (ValueError, TypeError):
        return "#"


def create_app(service: Service, *, background: bool = True) -> FastAPI:
    config = service.config
    editor=ConfigEditor(config.file_path)
    csrf_secret=secrets.token_bytes(32)
    def csrf_token():
        nonce=secrets.token_hex(32)
        return nonce+'.'+hmac.new(csrf_secret,nonce.encode(),hashlib.sha256).hexdigest()
    def valid_csrf(value):
        if not isinstance(value,str) or len(value)!=129:return False
        nonce,_,signature=value.partition('.')
        if len(nonce)!=64 or len(signature)!=64 or any(c not in '0123456789abcdef' for c in nonce+signature):return False
        return hmac.compare_digest(signature,hmac.new(csrf_secret,nonce.encode(),hashlib.sha256).hexdigest())

    async def protected_json(request, cookie):
        expected = f'{request.url.scheme}://{request.url.netloc}'
        token = request.headers.get('x-csrf-token', '')
        if (request.headers.get('origin') != expected or
                request.headers.get('sec-fetch-site', 'same-origin') != 'same-origin' or
                not valid_csrf(token) or
                not hmac.compare_digest(token.encode(), request.cookies.get(cookie, '').encode())):
            raise HTTPException(403, 'Same-origin request and valid CSRF token required')
        if request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise HTTPException(415, 'JSON required')
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 32768:
                raise HTTPException(413, 'Configuration request too large')
            raw.extend(chunk)
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError('Expected JSON object')
            return payload
        except (ValueError, UnicodeError):
            raise HTTPException(422, 'Invalid JSON request') from None
    def overall(now):
        try:return aggregate(config,service.detector_names,service.source_views(now),service.store.health_rows(),now)
        except Exception:return {'status':'red','label':'Action needed','summary':'Central health/database read failed','issues':['Central health/database read failed'],
                                'checked_at':now.isoformat(),'evaluated_at':None}
    def selection(request):
        selected=request.query_params.getlist('types') if request.query_params.get('filter')=='1' else list(DETECTORS)
        if set(selected)-set(DETECTORS):raise HTTPException(422,'Unknown event filter')
        return selected

    async def worker(stop: asyncio.Event, lane: str):
        while not stop.is_set():
            try:
                if lane == "delivery":
                    await asyncio.to_thread(service.dispatch, service.clock())
                    service.store.health('worker:delivery','ok',service.clock(),'Delivery policy evaluation completed')
                else:
                    await asyncio.to_thread(service.tick, lane=lane, force=False, deliver=False)
            except Exception as exc:
                service.store.health(f"worker:{lane}", "error", service.clock(), type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=15 if lane == "delivery" else service.lane_interval(lane))
            except TimeoutError:
                pass

    @asynccontextmanager
    async def lifespan(app):
        if not background:
            yield
            return
        with ServiceLock(config.data_dir):
            service.recover()
            service.camera.start()
            stop = asyncio.Event()
            # Slow owns shared atmosphere even when only live detectors are enabled.
            lanes = ["slow", *sorted({service.lane(d) for d in service.detectors}-{"slow"}), "delivery"]
            tasks = [asyncio.create_task(worker(stop, lane)) for lane in lanes]
            try:
                yield
            finally:
                stop.set()
                try:
                    await asyncio.to_thread(service.camera.close)
                    await asyncio.gather(*tasks)
                finally:
                    service.close()

    app = FastAPI(title="All Sky Alert", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(ExactHostMiddleware, allowed_hosts=config.values["service"]["allowed_hosts"])
    app.add_middleware(LANMiddleware,enabled=config.values['service']['lan_access'])
    directory = Path(__file__).parent
    app.mount("/static", StaticFiles(directory=directory / "static"), name="static")
    templates = Jinja2Templates(directory=directory / "templates")
    templates.env.filters["localtime"] = lambda value: parse(value).astimezone(ZoneInfo(config.observer.timezone)).strftime("%a %b %d %Y, %I:%M %p %Z")
    templates.env.filters["safe_url"] = safe_url
    templates.env.filters["precisetime"] = lambda value: parse(value).astimezone(ZoneInfo(config.observer.timezone)).strftime("%Y-%m-%d %H:%M:%S.%f %Z")

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        tile_origin=urlparse(config.values['map']['tile_url']).scheme+'://'+urlparse(config.values['map']['tile_url']).netloc
        is_map=request.url.path=='/aircraft'
        styles="'self' 'unsafe-inline'" if is_map else "'self'"
        response.headers["Content-Security-Policy"] = f"default-src 'self'; script-src 'self'; style-src {styles}; img-src 'self' data:{' '+tile_origin if is_map and config.values['map']['tiles_enabled'] else ''}; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin" if is_map else "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    def context(request: Request, **extra):
        return {"request": request, "timezone": config.observer.timezone,
                "observer": config.observer,'overall':overall(service.clock()),'map_enabled':config.values['map']['enabled'] and 'adsb' in service.detector_names, **extra}

    @app.get("/")
    def index(request: Request):
        now = service.clock()
        selected=selection(request)
        upcoming, recent = service.upcoming(now), service.recent(now)
        upcoming=[p for p in upcoming if p['candidate']['detector'] in selected]
        for prediction in upcoming + recent:
            validity = prediction["candidate"].get("valid_until")
            prediction["inputs_expired"] = bool(validity and now > parse(validity))
            detail = service.store.detail(prediction["candidate"]["id"], config.observer.key)
            prediction["alert_status"] = ", ".join(a["stage"] for a in detail["alerts"]) or "No alert"
        return templates.TemplateResponse(request=request, name="index.html", context=context(request,
            upcoming=[p for p in upcoming if parse(p["candidate"]["start"]) > now],
            active=[p for p in upcoming if parse(p["candidate"]["start"]) <= now and p["candidate"]["detector"] != "adsb"],
            aircraft_enabled="adsb" in service.detector_names and 'adsb' in selected,
            filter_choices=DETECTORS,selected_types=selected,digest_status=service.digest_status(now),
            recent=recent, recent_hours=config.values["service"]["recent_hours"],
            inbox=service.store.inbox(config.observer.key), health=service.store.health_rows(), sources=service.source_views(now)))

    @app.get("/events/{event_id}")
    def detail(event_id: str, request: Request):
        prediction = service.store.detail(event_id, config.observer.key)
        if not prediction:
            raise HTTPException(404, "Prediction not found")
        validity = prediction["candidate"].get("valid_until")
        prediction["inputs_expired"] = bool(validity and service.clock() > parse(validity))
        return templates.TemplateResponse(request=request, name="detail.html", context=context(request, p=prediction))

    @app.get("/api/predictions")
    def predictions(request: Request):
        now = service.clock()
        selected=selection(request)
        upcoming = [p for p in service.upcoming(now) if p['candidate']['detector'] in selected]
        # Keep the existing upcoming API inclusive of active windows.
        return {"upcoming": upcoming, "active": [p for p in upcoming if parse(p["candidate"]["start"]) <= now and
            (p["candidate"]["detector"] != "adsb" or not p["candidate"].get("valid_until") or now <= parse(p["candidate"]["valid_until"]))], "recent": service.recent(now)}

    @app.get("/api/live")
    def live():
        now = service.clock()
        rows = service.store.predictions(config.observer.key,["adsb"] if "adsb" in service.detector_names else [],now,2)
        aircraft = []
        for p in rows:
            e = p["candidate"]
            if e.get("valid_until") and now > parse(e["valid_until"]):
                continue
            if now < parse(e["metadata"]["position_at"]):
                continue
            aircraft.append({"id":e["id"],"title":e["title"],"prose":e["metadata"]["prose"],
                "track_state":e["metadata"]["track_state"],"position_at":e["metadata"]["position_at"],
                "position_age_seconds":round((now-parse(e["metadata"]["position_at"])).total_seconds(),1),
                "geometry":e["geometry"],"interest":e["interest"],"valid_until":e["valid_until"]})
        return {"checked_at":now.isoformat(),"aircraft":aircraft,"timezone":config.observer.timezone,
                "note":"Fresh recorded positions, not confirmed visual sightings. Unknown routes are never inferred."}

    @app.get("/api/events/{event_id}")
    def event(event_id: str):
        prediction = service.store.detail(event_id, config.observer.key)
        if prediction is None:
            raise HTTPException(404, "Prediction not found")
        return prediction

    @app.get("/api/events/{event_id}/revisions/{revision_id}")
    def revision(event_id: str, revision_id: int):
        data = service.store.revision(event_id, revision_id, config.observer.key)
        if data is None:
            raise HTTPException(404, "Revision not found")
        return data

    @app.get("/api/health")
    def health():
        now=service.clock()
        status=overall(now)
        try:return {"overall":status,"components":service.store.health_rows(),"sources":service.source_views(now),"camera":service.camera.snapshot()}
        except Exception:return JSONResponse({'overall':status,'components':[],'sources':[]},status_code=503)

    @app.get('/aircraft')
    def aircraft_page(request:Request):
        if not config.values['map']['enabled'] or 'adsb' not in service.detector_names:raise HTTPException(404,'Aircraft map is disabled')
        return templates.TemplateResponse(request=request,name='aircraft.html',context=context(request))

    @app.get('/api/aircraft')
    def aircraft_data():
        if not config.values['map']['enabled'] or 'adsb' not in service.detector_names:raise HTTPException(404,'Aircraft map is disabled')
        return map_snapshot(service,service.clock())

    @app.get('/api/map-config')
    def map_config():
        if not config.values['map']['enabled']:raise HTTPException(404,'Map disabled')
        return {key:config.values['map'][key] for key in ('tiles_enabled','tile_url','tile_attribution')}

    @app.get('/settings')
    def settings_page(request:Request):
        try:disk,revision,_=editor.read()
        except (ValueError,OSError):raise HTTPException(503,'Settings need a readable configured TOML file') from None
        token=csrf_token()
        fields={key:{**spec,'value':get_value(disk.values,key)} for key,spec in field_definitions().items()}
        response=templates.TemplateResponse(request=request,name='settings.html',context=context(request,
            fields=fields,csrf=token,revision=revision,categories=DETECTORS,
            restart_required=runtime_identity(disk)!=runtime_identity(config),digest_status=service.digest_status(service.clock())))
        response.set_cookie('asa_csrf',token,httponly=True,samesite='strict',secure=request.url.scheme=='https',path='/settings',max_age=3600)
        return response

    @app.post('/settings')
    async def save_settings(request:Request):
        payload = await protected_json(request, 'asa_csrf')
        try:
            if not isinstance(payload,dict) or set(payload)!={'revision','changes'} or not isinstance(payload['revision'],str):raise ValueError('Invalid settings request')
            _,changed=editor.save(payload['changes'],payload['revision'])
        except RuntimeError:raise HTTPException(409,'Configuration changed; reload Settings') from None
        except (ValueError,TypeError,KeyError,OverflowError):raise HTTPException(422,'Invalid or protected configuration setting; nothing saved') from None
        except OSError:raise HTTPException(503,'Atomic configuration save failed; check file permissions') from None
        service.store.health('configuration','restart_required',service.clock(),'Saved operational keys: '+', '.join(changed))
        return {'status':'saved','restart_required':True,'changed':changed,'message':'Saved — restart required. Running settings are unchanged.'}

    @app.get('/api/notifications')
    def notification_status():
        return {'scheduled':service.digest_status(service.clock()),'timezone':config.observer.timezone}

    @app.get('/camera')
    def camera_page(request: Request):
        token = csrf_token()
        try:
            disk, revision, _ = editor.read()
            saved = disk.values['camera']['timelapse']
        except (ValueError, OSError):
            revision, saved = None, config.values['camera']['timelapse']
        response = templates.TemplateResponse(request=request, name='camera.html', context=context(request,
            csrf=token, camera=service.camera.snapshot(), revision=revision, saved=saved))
        response.set_cookie('asa_camera_csrf', token, httponly=True, samesite='strict',
                            secure=request.url.scheme == 'https', path='/camera', max_age=3600)
        return response

    @app.get('/camera/status')
    def camera_status():
        return service.camera.snapshot()

    @app.get('/camera/latest')
    def camera_latest():
        with service.camera.lock:
            path = service.camera.latest_path
        if path is None or not path.is_file():
            raise HTTPException(404, 'No image captured during this service run')
        return FileResponse(path, media_type='image/jpeg', filename=path.name)

    @app.get('/camera/stream')
    async def camera_stream(request: Request):
        try:
            service.camera.subscribe()
        except CameraUnavailable as exc:
            raise HTTPException(503, str(exc)) from None
        except CameraBusy as exc:
            raise HTTPException(429, str(exc)) from None
        async def frames():
            previous = -1
            try:
                while not service.camera.stop_event.is_set() and not await request.is_disconnected():
                    with service.camera.lock:
                        if service.camera.state['state'] not in {'ready', 'capturing'}:
                            break
                    previous, frame = service.camera.preview_frame(previous)
                    if frame:
                        yield (b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' +
                               str(len(frame)).encode() + b'\r\n\r\n' + frame + b'\r\n')
                    await asyncio.sleep(1 / service.camera.settings['preview_fps'])
            finally:
                service.camera.unsubscribe()
        return StreamingResponse(frames(), media_type='multipart/x-mixed-replace; boundary=frame')

    @app.post('/camera/command')
    async def camera_command(request: Request):
        payload = await protected_json(request, 'asa_camera_csrf')
        action = payload.get('action')
        try:
            if action == 'capture' and set(payload) == {'action'}:
                key = service.camera.submit('capture')
            elif action == 'focus' and set(payload) <= {'action', 'mode', 'lens_position', 'trigger'} and 'mode' in payload:
                key = service.camera.submit('focus', mode=payload['mode'],
                    lens_position=payload.get('lens_position'), trigger=payload.get('trigger', False))
            elif action == 'timelapse' and set(payload) == {'action', 'running', 'interval_seconds'}:
                service.camera.set_timelapse(payload['running'], payload['interval_seconds'])
                return {'status': 'applied', 'message': 'Timelapse running state updated. An in-flight capture may finish; startup defaults are unchanged.'}
            elif action == 'save_timelapse' and set(payload) == {'action', 'enabled', 'interval_seconds', 'revision'} and isinstance(payload['revision'], str):
                _, changed = editor.save({'camera.timelapse.enabled': payload['enabled'],
                    'camera.timelapse.interval_seconds': payload['interval_seconds']}, payload['revision'])
                _, revision, _ = editor.read()
                service.store.health('configuration', 'restart_required', service.clock(), 'Saved operational keys: ' + ', '.join(changed))
                return {'status': 'saved', 'revision': revision, 'restart_required': True,
                        'message': 'Saved startup defaults — restart required. Use Start/Stop for this session.'}
            else:
                raise ValueError('Invalid camera command')
        except CameraUnavailable as exc:
            raise HTTPException(503, str(exc)) from None
        except CameraBusy as exc:
            raise HTTPException(429, str(exc)) from None
        except RuntimeError:
            raise HTTPException(409, 'Configuration changed; reload Camera before saving') from None
        except (ValueError, TypeError, KeyError, OverflowError):
            raise HTTPException(422, 'Invalid or unsupported camera setting; interval must be 10–604800 seconds') from None
        except OSError:
            raise HTTPException(503, 'Atomic configuration save failed; check directory permissions') from None
        return JSONResponse({'status': 'queued', 'id': key}, status_code=202)

    return app
