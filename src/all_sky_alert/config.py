from __future__ import annotations

import tomllib
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from .domain import Observer, bounded
from .registry import DETECTORS
from .sources import SOURCES
from .network import validate_network
from .camera.settings import DEFAULTS as CAMERA_DEFAULTS, validate as validate_camera
from .mail_policy import DEFAULTS as MAIL_DEFAULTS, migrate as migrate_mail, validate as validate_mail


DEFAULTS = {
    "service": {"data_dir": "data", "interval_seconds": 300, "recent_hours": 2,
                "prediction_days": 7, "retention_days": 90,
                "allowed_hosts": ["localhost", "127.0.0.1", "::1"]},
    "detectors": {name: spec.defaults for name, spec in DETECTORS.items()},
    "providers": {name: spec.defaults for name, spec in SOURCES.items()},
    "atmosphere": {"enabled": True, "cache_minutes": 60, "max_stale_hours": 6, "forecast_days": 10,
                   "canadian": {"enabled": False, "cache_minutes": 60, "max_stale_hours": 6,
                                "max_run_age_hours": 18, "max_grid_distance_km": 25}},
    "notifications": {"enabled": True, "minimum_interest": 35, "minimum_quality": 55,
                      "minimum_confidence": 0.3, "exceptional_interest": 90,
                      "watch_lead_hours": 168, "ready_lead_hours": 24,
                      "imminent_lead_minutes": 60, "overrides": {}},
    "gmail": {"enabled": False, "destination": "", "client_secret": "secrets/client_secret.json",
              "token": "secrets/gmail_token.json", **MAIL_DEFAULTS},
}
DEFAULTS["service"].update(bind_host="127.0.0.1", port=8008, lan_access=False, trusted_proxy_ips=[])
DEFAULTS["map"] = {"enabled": True, "tiles_enabled": True,
                   "tile_url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                   "tile_attribution": "OpenStreetMap contributors",
                   "radius_km": 40, "track_minutes": 10, "max_aircraft": 150, "max_points": 80}
DEFAULTS["camera"] = CAMERA_DEFAULTS


def merge(default: dict, supplied: dict, path: str = "") -> dict:
    result = deepcopy(default)
    for key, value in supplied.items():
        if key not in default:
            raise ValueError(f"Unknown configuration: {path}{key}")
        if isinstance(default[key], dict):
            if not isinstance(value, dict):
                raise ValueError(f"Expected table: {path}{key}")
            result[key] = value if key == "overrides" else merge(default[key], value, f"{path}{key}.")
        else:
            result[key] = value
    return result


@dataclass(frozen=True)
class Config:
    observer: Observer
    values: dict
    base: Path
    file_path: Path | None = None

    def path(self, value: str) -> Path:
        return (self.base / value).resolve()

    @property
    def data_dir(self) -> Path:
        return self.path(self.values["service"]["data_dir"])

    def policy(self, kind: str) -> dict:
        common = self.values["notifications"]
        return {**common, **common["overrides"].get(kind, {})}


def load(path: str | Path) -> Config:
    path = Path(path).resolve()
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return from_dict(raw, path.parent, path)


def from_dict(raw, base, file_path=None):
    raw = deepcopy(raw)
    observer = raw.pop("observer", None)
    if not observer or "latitude" not in observer or "longitude" not in observer or "timezone" not in observer:
        raise ValueError("Set observer.latitude, longitude and timezone in your configuration")
    if set(observer) - {"latitude", "longitude", "elevation_m", "timezone"}:
        raise ValueError("Unknown observer field")
    bounded(observer["latitude"], -90, 90, "latitude")
    bounded(observer["longitude"], -180, 180, "longitude")
    bounded(observer.get("elevation_m", 0), -500, 10000, "elevation_m")
    ZoneInfo(observer["timezone"])
    obs = Observer(**{"elevation_m": 0, **observer})
    if 'gmail' in raw:
        if not isinstance(raw['gmail'],dict):raise ValueError('Expected Gmail table')
        raw['gmail']=migrate_mail(raw['gmail'])
    values = merge(DEFAULTS, raw)
    validate_camera(values["camera"], base, values["service"]["data_dir"])
    service, atmosphere = values["service"], values["atmosphere"]
    for key, lo, hi in (("interval_seconds", 30, 86400), ("recent_hours", 0.1, 720),
                        ("prediction_days", 1, 365), ("retention_days", 1, 3650)):
        bounded(service[key], lo, hi, key)
    for key in ("prediction_days", "retention_days"):
        if not isinstance(service[key], int):
            raise ValueError(f"{key} must be an integer")
    if service["recent_hours"] > service["retention_days"] * 24:
        raise ValueError("History window must fit retention period")
    bounded(atmosphere["cache_minutes"], 1, 1440, "cache_minutes")
    bounded(atmosphere["max_stale_hours"], atmosphere["cache_minutes"] / 60, 48, "max_stale_hours")
    bounded(atmosphere["forecast_days"], 1, 16, "forecast_days")
    if not isinstance(atmosphere["forecast_days"], int):
        raise ValueError("forecast_days must be an integer")
    canadian = atmosphere["canadian"]
    bounded(canadian["cache_minutes"], 5, 1440, "canadian.cache_minutes")
    bounded(canadian["max_stale_hours"], canadian["cache_minutes"] / 60, 48, "canadian.max_stale_hours")
    bounded(canadian["max_run_age_hours"], 6, 48, "canadian.max_run_age_hours")
    bounded(canadian["max_grid_distance_km"], 1, 50, "canadian.max_grid_distance_km")
    for name, spec in DETECTORS.items():
        bounded(values["detectors"][name]["interval_seconds"], 5, 604800, f"{name}.interval_seconds")
        if spec.validate is not None:
            spec.validate(values["detectors"][name])
    for name, spec in SOURCES.items():
        spec.validate(values["providers"][name])
    orbit_ids = set(values["providers"]["orbits"]["catalog_ids"])
    if values["detectors"]["iss"]["enabled"] and not set(values["detectors"]["iss"]["catalog_ids"]).issubset(orbit_ids):
        raise ValueError("ISS/configured spacecraft catalog_ids must also be in providers.orbits.catalog_ids")
    if values["detectors"]["iss_transits"]["enabled"] and 25544 not in orbit_ids:
        raise ValueError("ISS transits require catalog 25544 in providers.orbits")
    for section in (values["notifications"], atmosphere, canadian, values["gmail"], *values["detectors"].values()):
        if not isinstance(section["enabled"], bool):
            raise ValueError("enabled must be a boolean")
    policies = [values["notifications"]]
    for kind, override in values["notifications"]["overrides"].items():
        if set(override) - (set(DEFAULTS["notifications"]) - {"overrides"}):
            raise ValueError(f"Unknown policy override for {kind}")
        policies.append({**values["notifications"], **override})
    for policy in policies:
        if not isinstance(policy["enabled"], bool):
            raise ValueError("Policy enabled must be a boolean")
        for key in ("minimum_interest", "minimum_quality", "exceptional_interest"):
            bounded(policy[key], 0, 100, key)
        bounded(policy["minimum_confidence"], 0, 1, "minimum_confidence")
        for key in ("watch_lead_hours", "ready_lead_hours", "imminent_lead_minutes"):
            bounded(policy[key], 0, 10000, key)
    if not isinstance(service["allowed_hosts"], list) or not service["allowed_hosts"] or not all(isinstance(h, str) and h and h != "*" for h in service["allowed_hosts"]):
        raise ValueError("allowed_hosts must list explicit hostnames")
    validate_network(service)
    m = values["map"]
    for key in ("enabled", "tiles_enabled"):
        if not isinstance(m[key],bool):
            raise ValueError("Map toggles must be boolean")
    from urllib.parse import urlsplit
    url = urlsplit(m["tile_url"])
    if url.scheme != "https" or not url.hostname or url.username or url.query or url.fragment or not all(v in url.path for v in ("{z}","{x}","{y}")):
        raise ValueError("Use a credential-free HTTPS tile template")
    if not isinstance(m['tile_attribution'],str) or not 1<=len(m['tile_attribution'])<=256 or any(ord(c)<32 for c in m['tile_attribution']):
        raise ValueError("Use a short plain-text tile attribution")
    for key,lo,hi in (("radius_km",1,185),("track_minutes",1,30),("max_aircraft",1,300),("max_points",2,180)):
        bounded(m[key],lo,hi,key)
        if key != "radius_km" and not isinstance(m[key],int):
            raise ValueError("Map bounds must be integers")
    validate_mail(values['gmail'],service['allowed_hosts'])
    recipient = values["gmail"]["destination"]
    if any(c in recipient for c in "\r\n") or (recipient and ("@" not in recipient or "," in recipient or ";" in recipient)):
        raise ValueError("Set one valid Gmail destination address")
    return Config(obs, values, Path(base), file_path)
