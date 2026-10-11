"""One shared, persistent weather acquisition path; validated untrusted inputs."""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx

from .domain import Conditions, Metric, Observer, Source, iso, parse
from .storage import Store, fingerprint
from .source_health import FreshnessPolicy, SourceHealth


FIELDS = {
    "cloud_cover": ("cloud_total", "%", 0, 100),
    "cloud_cover_low": ("cloud_low", "%", 0, 100),
    "cloud_cover_mid": ("cloud_mid", "%", 0, 100),
    "cloud_cover_high": ("cloud_high", "%", 0, 100),
    "relative_humidity_2m": ("humidity", "%", 0, 100),
    "visibility": ("visibility", "m", 0, 1000000),
    "wind_speed_10m": ("wind_surface", "m/s", 0, 200),
    "wind_speed_250hPa": ("wind_250hpa", "m/s", 0, 250),
    "temperature_2m": ("temperature_surface", "°C", -100, 65),
    "dew_point_2m": ("dewpoint_surface", "°C", -110, 65),
}


@dataclass(frozen=True)
class CachedResult:
    data: dict | None
    fetched_at: datetime | None
    status: str
    error: str | None


def cached_fetch(store, key, fetch, validate, now, ttl_seconds, max_age_seconds):
    """Persistent validated cache/backoff shared by weather and seeing requests."""
    cache = store.cache(key)
    data, fetched_at = None, None
    if cache and cache["payload"] is not None and cache["fetched_at"]:
        try:
            validate(cache["payload"])
            data, fetched_at = cache["payload"], parse(cache["fetched_at"])
        except (ValueError, TypeError, KeyError):
            pass
    age = (now - fetched_at).total_seconds() if fetched_at else math.inf
    fresh = data is not None and 0 <= age < ttl_seconds
    deferred = cache and cache["next_attempt"] and parse(cache["next_attempt"]) > now
    if not fresh and not deferred:
        try:
            data_new = fetch()
            validate(data_new)
            store.cache_success(key, data_new, now)
            data, fetched_at, fresh, age = data_new, now, True, 0
            cache = None
        except Exception as exc:
            failures = (cache["failures"] if cache else 0) + 1
            seconds = min(3600, 60 * 2 ** min(failures, 6))
            if isinstance(exc, httpx.HTTPStatusError):
                header = exc.response.headers.get("Retry-After", "")
                try:
                    seconds = max(seconds, min(86400, int(header)))
                except ValueError:
                    try:
                        seconds = max(seconds, min(86400, (parsedate_to_datetime(header) - now).total_seconds()))
                    except (ValueError, TypeError):
                        pass
            error = type(exc).__name__
            if isinstance(exc, httpx.HTTPStatusError):
                error += f" HTTP {exc.response.status_code}"
            store.cache_failure(key, failures, now + timedelta(seconds=seconds), error)
            cache = store.cache(key)
    status = "fresh" if fresh else "stale" if data is not None and 0 <= age <= max_age_seconds else "unavailable"
    return CachedResult(data if status != "unavailable" else None, fetched_at, status,
                        cache["error"] if cache else None)


class OpenMeteo:
    url = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, client: httpx.Client):
        self.client = client

    def fetch(self, observer: Observer, days: int) -> dict:
        response = self.client.get(self.url, params={
            "latitude": observer.latitude, "longitude": observer.longitude,
            "elevation": observer.elevation_m, "hourly": ",".join(FIELDS),
            "timezone": "UTC", "timeformat": "unixtime", "wind_speed_unit": "ms",
            "forecast_days": days, "past_days": 2,
        })
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            raise ValueError("Oversized forecast")
        data = response.json()
        self.validate(data)
        return data

    @staticmethod
    def validate(data: dict):
        if not isinstance(data, dict) or not isinstance(data.get("hourly"), dict):
            raise ValueError("Missing hourly forecast")
        hourly = data["hourly"]
        times = hourly.get("time")
        if not isinstance(times, list) or not 1 <= len(times) <= 24 * 20:
            raise ValueError("Invalid forecast time axis")
        last = None
        for timestamp in times:
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
                raise ValueError("Invalid timestamp")
            datetime.fromtimestamp(timestamp, UTC)
            if last is not None and timestamp <= last:
                raise ValueError("Nonascending time axis")
            last = timestamp
        units = data.get("hourly_units", {})
        for field, (_, unit, low, high) in FIELDS.items():
            values = hourly.get(field)
            if not isinstance(values, list) or len(values) != len(times) or units.get(field) != unit:
                raise ValueError("Invalid field shape or units")
            for value in values:
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or
                                          not math.isfinite(value) or not low <= value <= high):
                    raise ValueError("Invalid metric range")


class ConditionsService:
    def __init__(self, store: Store, provider: OpenMeteo, observer: Observer, settings: dict, seeing=None):
        self.store, self.provider, self.observer, self.settings = store, provider, observer, settings
        self.key = fingerprint({"provider": "open-meteo-v1", "observer": observer.key,
                                "fields": list(FIELDS), "days": settings["forecast_days"]})
        self.data = None
        self.fetched_at = None
        self.status = "unavailable"
        self.now = None
        self.seeing = seeing
        self._frame = (None, None, "unavailable", None)
        self.consumers = ["planetary", "sunset"]

    def refresh(self, now: datetime) -> None:
        self.now = now
        if not self.settings["enabled"]:
            self.data, self.status = None, "disabled"
            self._frame = (None, None, "disabled", now)
            SourceHealth("open-meteo", "forecast", iso(now), FreshnessPolicy(self.settings["cache_minutes"]*60,
                self.settings["cache_minutes"]*60, self.settings["max_stale_hours"]*3600), self.consumers,
                available=False, details={"disabled":True}).record(self.store)
            self.store.health("atmosphere", "disabled", now, "Weather acquisition disabled")
            if self.seeing:
                self.seeing.disable(now)
            return
        result = cached_fetch(self.store, self.key,
            lambda: self.provider.fetch(self.observer, self.settings["forecast_days"]), OpenMeteo.validate,
            now, self.settings["cache_minutes"] * 60, self.settings["max_stale_hours"] * 3600)
        self.data, self.fetched_at, self.status = result.data, result.fetched_at, result.status
        # One immutable frame pointer publishes a coherent snapshot to other lanes.
        self._frame = (self.data, self.fetched_at, self.status, now)
        first = datetime.fromtimestamp(self.data["hourly"]["time"][0], UTC) if self.data else None
        last = datetime.fromtimestamp(self.data["hourly"]["time"][-1], UTC) if self.data else None
        SourceHealth("open-meteo", "forecast", iso(now), FreshnessPolicy(self.settings["cache_minutes"]*60,
            self.settings["cache_minutes"]*60, self.settings["max_stale_hours"]*3600), self.consumers,
            retrieved_at=iso(self.fetched_at) if self.fetched_at else None,
            valid_from=iso(first) if first else None, valid_until=iso(last) if last else None,
            next_refresh=iso(self.fetched_at+timedelta(minutes=self.settings["cache_minutes"])) if self.fetched_at else None,
            cached=self.fetched_at != now, cache_status=self.status, error=result.error, available=self.data is not None,
            details={"freshness_basis":"retrieval", "upstream_issue_time":"not supplied by this adapter"}).record(self.store)
        message = f"Forecast retrieved {iso(self.fetched_at)}" if self.fetched_at else "No usable forecast"
        if result.error:
            message += f"; {result.error}"
        self.store.health("atmosphere", self.status, now, message)
        if self.seeing:
            try:
                self.seeing.refresh(now)
            except Exception as exc:
                self.store.health("Canadian seeing", "error", now, type(exc).__name__)

    def prepare(self, times: list[datetime]):
        if self.settings["enabled"] and self.seeing:
            try:
                self.seeing.prepare(times)
            except Exception as exc:
                self.store.health("Canadian seeing", "error", self.now, type(exc).__name__)

    def at(self, when: datetime, geometry: dict) -> Conditions:
        return self.snapshot_at(when, geometry, self.now)

    def snapshot_at(self, when, geometry, now):
        base = self.weather_at(when, geometry, now)
        if not self.settings["enabled"] or not self.seeing:
            return base
        try:
            extra = self.seeing.at(when, geometry)
        except Exception:
            return replace(base, notes=base.notes + ["Canadian seeing snapshot unavailable"])
        index = extra.metrics.get("seeing_index")
        deadline = parse(index.issued_at)+timedelta(hours=self.seeing.settings["max_run_age_hours"]) if index and index.issued_at else None
        if deadline and now and now > deadline:
            extra = replace(extra, metrics={}, notes=extra.notes+["Canadian model run exceeds its age limit"])
            deadline = None
        return replace(base, metrics={**base.metrics, **extra.metrics},
                       additional_sources=([extra.source] if extra.source else []),
                       notes=base.notes + extra.notes)

    def weather_at(self, when: datetime, geometry: dict, now=None) -> Conditions:
        data, fetched_at, status, evaluated = self._frame
        now = now if now is not None else evaluated
        if data and now is not None:
            age = (now-fetched_at).total_seconds()
            if age < 0 or age > self.settings["max_stale_hours"]*3600:
                data, status = None, "unavailable"
            elif age >= self.settings["cache_minutes"]*60:
                status = "stale"
        if not data:
            return Conditions({}, status, None, None, ["Atmospheric conditions unknown"])
        times = data["hourly"]["time"]
        timestamp = when.timestamp()
        if timestamp < times[0] or timestamp > times[-1]:
            return Conditions({}, "outside_forecast", iso(fetched_at), None, ["Event lies outside available forecast"])
        index = min(range(len(times)), key=lambda i: abs(times[i] - timestamp))
        if abs(times[index] - timestamp) > 3600:
            return Conditions({}, "unavailable", iso(fetched_at), None, ["Gap in forecast"])
        valid = datetime.fromtimestamp(times[index], UTC)
        lead_days = max(0, (when - now).total_seconds() / 86400)
        confidence = max(0.25, 0.8 - lead_days * 0.06)
        if status == "stale":
            confidence *= 0.35
        metrics = {}
        for field, (name, unit, _, _) in FIELDS.items():
            value = data["hourly"][field][index]
            if value is not None:
                metrics[name] = Metric(value, unit, round(confidence, 3), "Open-Meteo", "hourly model forecast; nearest sample", iso(valid),
                                       status=status, retrieved_at=iso(fetched_at))
        source = Source("Open-Meteo", "https://open-meteo.com/", iso(fetched_at), "Best-match forecast; CC BY 4.0")
        notes = ["Grid cloud fraction is not line-of-sight or horizon obstruction", "Seeing in arcseconds and astronomical extinction are unknown"]
        return Conditions(metrics, status, iso(fetched_at), source, notes,
                          valid_until=iso(fetched_at+timedelta(minutes=self.settings["cache_minutes"])))
