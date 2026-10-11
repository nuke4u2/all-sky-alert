"""Replaceable GP adapter and normalized OMM inputs; no geometry or delivery."""
from dataclasses import dataclass
from datetime import timedelta
from threading import Lock
from typing import Protocol
import json

import httpx

from .domain import bounded, iso, parse
from .providers import cached_fetch
from .source_health import FreshnessPolicy, SourceHealth
from .storage import fingerprint


@dataclass(frozen=True)
class OrbitRecord:
    catalog_id: int
    name: str
    epoch: str
    elements: dict
    source_url: str
    retrieved_at: str

    @property
    def identity(self):
        return fingerprint(self.elements)


class OrbitSource(Protocol):
    def refresh(self, now): ...
    def records(self, now) -> dict[int, OrbitRecord]: ...


def normalize_omm(row):
    """CelesTrak's omitted constants are documented EARTH/TEME/UTC/SGP4."""
    if not isinstance(row, dict):
        raise ValueError("Expected GP record")
    result = {}
    for key, default in (("CENTER_NAME", "EARTH"), ("REF_FRAME", "TEME"),
                         ("TIME_SYSTEM", "UTC"), ("MEAN_ELEMENT_THEORY", "SGP4")):
        if row.get(key, default) != default:
            raise ValueError("Unsupported orbit frame/time/theory")
        result[key] = default
    epoch = row["EPOCH"]
    # OMM UTC epochs commonly omit the UTC suffix by specification.
    if not isinstance(epoch, str) or len(epoch) > 40:
        raise ValueError("Invalid epoch")
    parsed = parse(epoch if epoch.endswith("Z") or "+" in epoch[10:] else epoch + "Z")
    result["EPOCH"] = iso(parsed).removesuffix("+00:00")
    for key, low, high in (("NORAD_CAT_ID", 1, 999999999), ("MEAN_MOTION", .01, 18),
            ("ECCENTRICITY", 0, .999), ("INCLINATION", 0, 180),
            ("RA_OF_ASC_NODE", 0, 360), ("ARG_OF_PERICENTER", 0, 360),
            ("MEAN_ANOMALY", 0, 360), ("BSTAR", -1, 1),
            ("MEAN_MOTION_DOT", -10, 10), ("MEAN_MOTION_DDOT", -10, 10),
            ("EPHEMERIS_TYPE", 0, 0), ("ELEMENT_SET_NO", 0, 999999999),
            ("REV_AT_EPOCH", 0, 999999999)):
        value = row[key]
        if isinstance(value, bool):
            raise ValueError("Invalid orbital number")
        value = float(value)
        bounded(value, low, high, key)
        if key in {"NORAD_CAT_ID", "EPHEMERIS_TYPE", "ELEMENT_SET_NO", "REV_AT_EPOCH"}:
            if value != int(value):
                raise ValueError("Expected integer orbital field")
            value = int(value)
        result[key] = value
    for key, default in (("OBJECT_NAME", "Unnamed spacecraft"), ("OBJECT_ID", ""), ("CLASSIFICATION_TYPE", "U")):
        value = row.get(key, default)
        if not isinstance(value, str) or len(value) > 128 or any(ord(c) < 32 for c in value):
            raise ValueError("Invalid orbital text")
        result[key] = value
    return result


class CelesTrak:
    defaults = {"enabled": True, "catalog_ids": [25544], "cache_seconds": 7200,
                "warning_hours": 24, "stale_hours": 48, "invalid_hours": 96,
                "transit_current_hours": 12, "transit_target_hours": 24}
    url = "https://celestrak.org/NORAD/elements/gp.php"

    @staticmethod
    def validate_settings(s):
        if not isinstance(s["enabled"], bool):
            raise ValueError("orbits.enabled must be boolean")
        ids = s["catalog_ids"]
        if not isinstance(ids, list) or not 1 <= len(ids) <= 10 or len(set(ids)) != len(ids):
            raise ValueError("Choose 1–10 unique catalog IDs")
        for value in ids:
            bounded(value, 1, 999999999, "catalog_id")
            if not isinstance(value, int):
                raise ValueError("Catalog IDs must be integers")
        bounded(s["cache_seconds"], 7200, 86400, "orbits.cache_seconds")
        bounded(s["warning_hours"], 1, 48, "orbits.warning_hours")
        bounded(s["stale_hours"], s["warning_hours"], 72, "orbits.stale_hours")
        bounded(s["invalid_hours"], s["stale_hours"], 168, "orbits.invalid_hours")
        bounded(s["transit_current_hours"], 1, 12, "orbits.transit_current_hours")
        bounded(s["transit_target_hours"], s["transit_current_hours"], 24, "orbits.transit_target_hours")

    def __init__(self, client, store, observer, settings, consumers):
        self.client, self.store, self.settings, self.consumers = client, store, settings, consumers
        self._lock, self._frame = Lock(), {}
        self.health = None
        self._geometries = {}

    def key(self, catalog_id):
        return f"celestrak-gp/v1/{catalog_id}"

    def fetch(self, catalog_id, now):
        stop = self.store.cache("celestrak-stop/v1")
        if stop:
            raise RuntimeError("CelesTrak HTTP stop requires operator reset")
        with self.client.stream("GET", self.url, params={"CATNR":catalog_id, "FORMAT":"JSON"},
                                timeout=httpx.Timeout(8, connect=4)) as response:
            # CelesTrak requests that M2M clients stop on EVERY non-200 response.
            if response.status_code != 200:
                self.store.cache_success("celestrak-stop/v1", {"status":response.status_code}, now)
                response.raise_for_status()
                raise ValueError("CelesTrak non-200 response")
            parts, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 128000:
                    raise ValueError("Oversized GP response")
                parts.append(chunk)
            rows = json.loads(b"".join(parts))
        self.validate_payload(rows, catalog_id)
        return rows

    @staticmethod
    def validate_payload(rows, catalog_id):
        if not isinstance(rows, list) or len(rows) != 1 or normalize_omm(rows[0])["NORAD_CAT_ID"] != catalog_id:
            raise ValueError("Unexpected GP catalog response")

    def refresh(self, now):
        with self._lock:
            frame = {}
            for catalog_id in self.settings["catalog_ids"]:
                policy = FreshnessPolicy(self.settings["cache_seconds"], self.settings["stale_hours"]*3600,
                                         self.settings["invalid_hours"]*3600)
                if not self.settings["enabled"]:
                    health = SourceHealth(f"orbits.{catalog_id}", "orbit", iso(now), policy, self.consumers,
                        available=False, details={"disabled":True})
                    record = None
                else:
                    previous = self.store.cache(self.key(catalog_id))
                    result = cached_fetch(self.store, self.key(catalog_id), lambda: self.fetch(catalog_id, now),
                        lambda rows: self.validate_payload(rows, catalog_id), now, policy.cadence_seconds,
                        policy.invalid_after_seconds)
                    record = None
                    if result.data:
                        row = normalize_omm(result.data[0])
                        record = OrbitRecord(catalog_id, row["OBJECT_NAME"], iso(parse(row["EPOCH"]+"Z")), row,
                            f"{self.url}?CATNR={catalog_id}&FORMAT=JSON", iso(result.fetched_at))
                    cache = self.store.cache(self.key(catalog_id))
                    new_failure = cache and cache["failures"] > (previous["failures"] if previous else 0)
                    if result.error and new_failure and cache["next_attempt"] and parse(cache["next_attempt"]) < now+timedelta(seconds=policy.cadence_seconds):
                        self.store.cache_failure(self.key(catalog_id),cache["failures"],now+timedelta(seconds=policy.cadence_seconds),cache["error"])
                        cache = self.store.cache(self.key(catalog_id))
                    stop = self.store.cache("celestrak-stop/v1")
                    health = SourceHealth(f"orbits.{catalog_id}", "orbit", iso(now), policy, self.consumers,
                        retrieved_at=iso(result.fetched_at) if result.fetched_at else None,
                        data_at=record.epoch if record else None,
                        valid_until=iso(parse(record.epoch)+timedelta(hours=self.settings["invalid_hours"])) if record else None,
                        cached=result.fetched_at != now, available=record is not None, error=result.error,
                        next_refresh=None if stop else cache["next_attempt"] if cache and cache["next_attempt"] else
                            iso(result.fetched_at+timedelta(seconds=policy.cadence_seconds)) if result.fetched_at else None,
                        details={"element_epoch":record.epoch if record else None, "warning_hours":self.settings["warning_hours"],
                            "freshness_basis":"element_epoch", "http_stopped":bool(stop),
                            "http_stop_status":stop["payload"]["status"] if stop else None,
                            "covariance":"Not supplied by GP; no calibrated timing or track uncertainty"})
                health.record(self.store)
                frame[catalog_id] = (record, health)
                self.health = health
            self._frame = frame

    def records(self, now):
        return {key: record for key, (record, health) in self._frame.items() if record and
                health.view(now)["status"] not in {"invalid", "unavailable", "disabled"}}

    @property
    def cache_identity(self):
        from .orbits import asset_info
        from importlib.metadata import version
        return fingerprint({"records":{str(key):record.identity for key, (record, _) in self._frame.items() if record},
                            "assets":asset_info(),"sgp4":version("sgp4")})

    def geometry(self, record, observer):
        from .orbits import OrbitGeometry,asset_info
        key = (record.identity, observer.key)
        if key not in self._geometries:
            if len(self._geometries) >= 20:
                self._geometries.clear()
            self._geometries[key] = OrbitGeometry(record, observer)
        SourceHealth("local.orbital_geometry","local",self.health.checked_at if self.health else record.retrieved_at,
            FreshnessPolicy(86400,None,None),self.consumers,valid_from="1900-01-01T00:00:00+00:00",
            valid_until="2053-10-08T00:00:00+00:00",details=asset_info()).record(self.store)
        return self._geometries[key]

    def reset_stop(self):
        # Explicit CLI only; never reset automatically or at restart.
        self.store.delete_cache("celestrak-stop/v1")
