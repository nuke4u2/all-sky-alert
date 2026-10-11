"""Offline installed-package smoke; pass a read-only fixture directory as argv[1]."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import timedelta
import json
import os
import sys
import threading
import time

import httpx
import uvicorn

from all_sky_alert.config import Config, DEFAULTS
from all_sky_alert.domain import Observer, parse
from all_sky_alert.orbits import asset_info
from all_sky_alert.service import Service
from all_sky_alert.web import create_app


fixtures = Path(sys.argv[1])
omm = json.loads((fixtures / "iss-omm.json").read_text())
aircraft = json.loads((fixtures / "adsb-radius.json").read_text())
now = parse(omm[0]["EPOCH"] + "Z")
aircraft["now"] = now.timestamp() * 1000


def handler(request):
    if request.url.host == "celestrak.org":
        return httpx.Response(200, json=omm)
    if request.url.host == "api.adsb.lol":
        return httpx.Response(200, json=aircraft)
    raise AssertionError("Unexpected acquisition")


with TemporaryDirectory(prefix="all-sky-phase3-") as directory:
    values = deepcopy(DEFAULTS)
    for name, settings in values["detectors"].items():
        settings["enabled"] = name in {"iss", "iss_transits", "adsb"}
    values["atmosphere"]["enabled"] = False
    values["gmail"]["enabled"] = False
    config = Config(Observer(51.4779, 0, 46, "Europe/London"), values, Path(directory))
    service = Service(config, adapters=[], clock=lambda: now)
    service.client.close()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    service.client = client
    for source in service.sources.values():
        source.client = client
    service.tick(now, deliver=False)
    rows = service.upcoming(now)
    counts = {name: sum(p["candidate"]["detector"] == name for p in rows) for name in service.detector_names}
    assert counts["iss"] > 0 and counts["adsb"] > 0, counts
    identities = {p["candidate"]["id"] for p in rows}
    alerts = sum(len(service.store.detail(i, config.observer.key)["alerts"]) for i in identities)
    service.close()
    # Reconstruct providers/detectors against the same private SQLite file.
    service = Service(config, adapters=[], clock=lambda: now)
    service.client.close()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    service.client = client
    for source in service.sources.values():
        source.client = client
    service.tick(now + timedelta(seconds=5), deliver=False)
    assert {p["candidate"]["id"] for p in service.upcoming(now)} == identities
    assert sum(len(service.store.detail(i, config.observer.key)["alerts"]) for i in identities) == alerts
    server = uvicorn.Server(uvicorn.Config(create_app(service, background=False), host="127.0.0.1", port=8019, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        with httpx.Client(timeout=5) as browser:
            for _ in range(50):
                try:
                    if browser.get("http://127.0.0.1:8019/api/health").status_code == 200:
                        break
                except httpx.ConnectError:
                    time.sleep(.1)
            paths = ["/", "/api/predictions", "/api/live", "/api/health", "/static/live.js"]
            paths += ["/events/" + i for i in identities]
            for path in paths:
                browser.get("http://127.0.0.1:8019" + path).raise_for_status()
            assert browser.get("http://127.0.0.1:8019/api/live").json()["aircraft"]
        print(json.dumps({"uid": os.getuid() if hasattr(os, "getuid") else None, "counts": counts,
                          "http_routes_200": len(paths), "restart_identity_and_dedup": True,
                          "kernel": asset_info(), "gmail": False}, indent=2))
    finally:
        server.should_exit = True
        thread.join(10)
        service.close()
        assert not thread.is_alive()
