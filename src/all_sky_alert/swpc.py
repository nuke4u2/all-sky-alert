"""Shared SWPC product acquisition. OVATION is a forecast, not local observation."""
from datetime import timedelta
import json
from threading import Lock

import httpx

from .domain import bounded, iso, parse
from .providers import cached_fetch
from .source_health import FreshnessPolicy, SourceHealth
from .storage import fingerprint


class SWPC:
    defaults = {"enabled": True, "cache_seconds": 300, "stale_after_minutes": 30, "invalid_after_minutes": 60}
    url = "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json"
    name = "swpc.ovation"

    @staticmethod
    def validate_settings(settings):
        if not isinstance(settings["enabled"], bool):
            raise ValueError("SWPC enabled must be boolean")
        bounded(settings["cache_seconds"], 300, 3600, "swpc.cache_seconds")
        bounded(settings["stale_after_minutes"], 5, 60, "swpc.stale_after_minutes")
        bounded(settings["invalid_after_minutes"], settings["stale_after_minutes"], 120, "swpc.invalid_after_minutes")

    def __init__(self, client, store, settings, consumers):
        self.client, self.store, self.settings, self.consumers = client, store, settings, consumers
        self.key = "swpc-ovation/"+fingerprint({"version":1, "endpoint":self.url})
        self.data, self.health = None, None
        self._refresh_lock = Lock()
        self._frame = None

    @staticmethod
    def validate(data):
        observed, forecast = parse(data["Observation Time"]), parse(data["Forecast Time"])
        if not -timedelta(minutes=5) <= forecast-observed <= timedelta(hours=2):
            raise ValueError("Unexpected OVATION forecast lead")
        coordinates = data.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) != 360*181:
            raise ValueError("Incomplete OVATION global grid")
        seen = set()
        for cell in coordinates:
            if not isinstance(cell, list) or len(cell) != 3:
                raise ValueError("Invalid OVATION cell")
            lon, lat, value = cell
            bounded(lon, 0, 359, "OVATION longitude")
            bounded(lat, -90, 90, "OVATION latitude")
            bounded(value, 0, 100, "OVATION intensity proxy")
            if lon != int(lon) or lat != int(lat) or (lon,lat) in seen:
                raise ValueError("Invalid/duplicate OVATION grid cell")
            seen.add((lon,lat))

    def fetch(self):
        with self.client.stream("GET", self.url, timeout=httpx.Timeout(10, connect=5)) as response:
            response.raise_for_status()
            parts, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 2_000_000:
                    raise ValueError("Oversized OVATION response")
                parts.append(chunk)
            data = json.loads(b"".join(parts))
        self.validate(data)
        return data

    def refresh(self, now):
        # Shared consumers cannot race identical fetches; readers use an atomic frame.
        with self._refresh_lock:
            self._refresh(now)

    def _refresh(self, now):
        policy = FreshnessPolicy(self.settings["cache_seconds"], self.settings["stale_after_minutes"]*60,
                                 self.settings["invalid_after_minutes"]*60)
        if not self.settings["enabled"]:
            self.data = None
            self.health = SourceHealth(self.name, "forecast", iso(now), policy, self.consumers,
                                       available=False, details={"disabled":True})
        else:
            result = cached_fetch(self.store, self.key, self.fetch, self.validate, now,
                                  self.settings["cache_seconds"], policy.invalid_after_seconds)
            self.data = result.data
            observed = self.data["Observation Time"] if self.data else None
            forecast = parse(self.data["Forecast Time"]) if self.data else None
            retry = self.store.cache(self.key)
            next_refresh = (retry["next_attempt"] if retry and retry["next_attempt"] else
                iso(result.fetched_at+timedelta(seconds=policy.cadence_seconds)) if result.fetched_at else None)
            self.health = SourceHealth(self.name, "forecast", iso(now), policy, self.consumers,
                retrieved_at=iso(result.fetched_at) if result.fetched_at else None, data_at=observed,
                valid_from=iso(forecast-timedelta(minutes=20)) if forecast else None,
                valid_until=iso(forecast+timedelta(minutes=20)) if forecast else None,
                next_refresh=next_refresh, cached=result.fetched_at != now, error=result.error,
                available=self.data is not None,
                details={"forecast_time":iso(forecast) if forecast else None, "product":"OVATION",
                         "grid_proxy":"NOAA intensity-derived viewing proxy; not observer visual probability",
                         "validity_method":"Application opportunity window +/-20 min around forecast time; not a NOAA-provided interval",
                         "forcing_mode":"Solar wind or Kp fallback; not identified by this JSON"})
        self.health.record(self.store)
        self._frame = (self.data, self.health)

    def snapshot(self, now):
        frame = self._frame
        if not frame or frame[1].view(now)["status"] in {"invalid", "unavailable", "disabled"}:
            raise RuntimeError("No valid OVATION forecast")
        return frame[0], frame[1].view(now)
