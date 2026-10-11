"""ECCC RDPS native astronomical seeing categories via GeoMet point queries.

No chart/image scraping and no GRIB/native decoder. refresh/prepare acquire shared
data; at is a pure snapshot read. Categories are not measurements in arcseconds.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

import httpx

from .domain import Conditions, Metric, Observer, Source, bounded, iso, parse
from .providers import cached_fetch
from .storage import fingerprint
from .source_health import FreshnessPolicy, SourceHealth


LABELS = {0: "Cloud masked", 1: "Very bad", 2: "Poor", 3: "Average", 4: "Good", 5: "Excellent"}


def stamp(when: datetime) -> str:
    return iso(when).replace("+00:00", "Z")


def distance_km(lat1, lon1, lat2, lon2):
    a, b = math.radians(lat1), math.radians(lat2)
    h = math.sin((b-a)/2)**2 + math.cos(a)*math.cos(b)*math.sin(math.radians(lon2-lon1)/2)**2
    return 12742 * math.asin(math.sqrt(min(1, max(0, h))))


class GeoMetSeeing:
    url = "https://geo.weather.gc.ca/geomet"
    layer = "RDPS_10km_SeeingIndex"
    documentation = "https://eccc-msc.github.io/open-data/msc-data/nwp_rdps/readme_rdps-datamart_en/"

    def __init__(self, client: httpx.Client):
        self.client = client

    def request(self, params):
        # Fixed endpoint, bounded reads, no redirects or remote URL following.
        with self.client.stream("GET", self.url, params={"SERVICE": "WMS", "VERSION": "1.3.0", **params},
                                timeout=httpx.Timeout(8, connect=5)) as response:
            response.raise_for_status()
            parts, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 100_000:
                    raise ValueError("Oversized GeoMet response")
                parts.append(chunk)
            return b"".join(parts)

    def capabilities(self):
        raw = self.request({"REQUEST": "GetCapabilities", "LAYERS": self.layer})
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise ValueError("Unsafe XML declaration")
        root = ET.fromstring(raw)
        ns = {"w": "http://www.opengis.net/wms"}
        for layer in root.findall(".//w:Layer", ns):
            if layer.findtext("w:Name", namespaces=ns) != self.layer:
                continue
            dimensions = {d.attrib["name"]: d for d in layer.findall("w:Dimension", ns)}
            extent = (dimensions["time"].text or "").strip().split("/")
            if len(extent) != 3 or extent[2] != "PT1H":
                raise ValueError("Unexpected seeing time axis")
            data = {"run": iso(parse(dimensions["reference_time"].attrib["default"])),
                    "start": iso(parse(extent[0])), "end": iso(parse(extent[1]))}
            self.validate_capabilities(data)
            return data
        raise ValueError("Seeing layer missing")

    @staticmethod
    def validate_capabilities(data):
        run, start, end = (parse(data[k]) for k in ("run", "start", "end"))
        if run.hour not in (0, 6, 12, 18) or any(t.minute or t.second or t.microsecond for t in (run, start, end)):
            raise ValueError("Unexpected RDPS run/axis")
        if (not start <= end or (end-start) > timedelta(days=10) or
                start > run+timedelta(hours=84) or end < run+timedelta(hours=1)):
            raise ValueError("Invalid RDPS extent")

    def point(self, observer: Observer, run: datetime, valid: datetime, max_distance: float):
        import json
        # WMS 1.3 EPSG:4326 axis order is latitude,longitude. Odd pixel dimensions
        # put I/J exactly at the observer. A 1-degree canvas avoids GeoMet's
        # inaccurate grid selection observed with tiny 3x3 query canvases.
        if abs(observer.latitude) > 89.5 or abs(observer.longitude) > 179.5:
            raise ValueError("Point query unsupported at pole/dateline boundary")
        lat, lon = observer.latitude, observer.longitude
        raw = self.request({"REQUEST": "GetFeatureInfo", "LAYERS": self.layer, "QUERY_LAYERS": self.layer,
            "CRS": "EPSG:4326", "BBOX": f"{lat-.5},{lon-.5},{lat+.5},{lon+.5}",
            "WIDTH": 101, "HEIGHT": 101, "I": 50, "J": 50, "FORMAT": "image/png",
            "INFO_FORMAT": "application/json", "FEATURE_COUNT": 1,
            "TIME": stamp(valid), "DIM_REFERENCE_TIME": stamp(run)})
        response = json.loads(raw)
        if response.get("type") != "FeatureCollection" or response.get("layer") != self.layer:
            raise ValueError("Invalid GeoMet feature collection")
        features = response.get("features")
        if not isinstance(features, list) or len(features) > 1:
            raise ValueError("Invalid point features")
        if not features:
            return {"value": None, "run": iso(run), "valid": iso(valid), "grid": None}
        feature = features[0]
        properties, geometry = feature["properties"], feature["geometry"]
        if parse(properties["time"]) != valid or parse(properties["dim_reference_time"]) != run:
            raise ValueError("GeoMet returned a different forecast time/run")
        if geometry.get("type") != "Point":
            raise ValueError("Missing grid point")
        grid = geometry["coordinates"]
        result = {"value": properties.get("value"), "run": iso(run), "valid": iso(valid), "grid": grid}
        self.validate_point(result, observer, run, valid, max_distance)
        return result

    @staticmethod
    def validate_point(data, observer, run, valid, max_distance):
        if parse(data["run"]) != run or parse(data["valid"]) != valid:
            raise ValueError("Point cache time/run mismatch")
        value, grid = data["value"], data["grid"]
        if value is not None:
            bounded(value, 0, 5, "seeing index")
            if int(value) != value:
                raise ValueError("Seeing index must be a native category")
        if grid is None:
            if value is not None:
                raise ValueError("Seeing without a grid point")
            return
        if not isinstance(grid, list) or len(grid) != 2:
            raise ValueError("Invalid grid coordinates")
        bounded(grid[0], -180, 180, "grid longitude")
        bounded(grid[1], -90, 90, "grid latitude")
        if distance_km(observer.latitude, observer.longitude, grid[1], grid[0]) > max_distance:
            raise ValueError("GeoMet grid point too far from observer")


class CanadianSeeing:
    """Persistent model metadata and immutable per-run/hour/location point cache."""
    def __init__(self, store, provider: GeoMetSeeing, observer: Observer, settings: dict):
        self.store, self.provider, self.observer, self.settings = store, provider, observer, settings
        self.key = "eccc-metadata-v1"
        self.gate = "eccc-backoff-v1"
        self.metadata, self.samples = None, {}
        self.now, self.fetched_at, self.deadline = None, None, 0
        self.status = "unavailable"
        self.requests = 0
        self.consumers = ["planetary"]
        self.metadata_error = None

    def report_source(self, now, error=None):
        SourceHealth("eccc.seeing", "forecast", iso(now), FreshnessPolicy(self.settings["cache_minutes"]*60,
            self.settings["max_run_age_hours"]*3600, self.settings["max_run_age_hours"]*3600), self.consumers,
            retrieved_at=iso(self.fetched_at) if self.fetched_at else None,
            issued_at=self.metadata["run"] if self.metadata else None,
            valid_from=iso(self.start) if self.metadata else None, valid_until=iso(self.end) if self.metadata else None,
            next_refresh=iso(self.fetched_at+timedelta(minutes=self.settings["cache_minutes"])) if self.fetched_at else None,
            cached=self.fetched_at != now, cache_status=self.status, error=error or self.metadata_error, available=self.metadata is not None,
            details={"disabled":self.status == "disabled", "model_cadence_seconds":21600,
                     "metadata_cache_minutes":self.settings["cache_minutes"], "requested_hours":len(self.samples),
                     "point_requests":self.requests, "index":"0 cloud masked; native 1–5, not arcseconds"}).record(self.store)

    def disable(self, now):
        self.metadata, self.samples, self.status = None, {}, "disabled"
        self.metadata_error = None
        self.store.health("Canadian seeing", "disabled", now, "Seeing acquisition disabled")
        self.report_source(now)

    def refresh(self, now):
        self.now, self.samples, self.requests = now, {}, 0
        self.deadline = 0
        if not self.settings["enabled"]:
            self.disable(now)
            return
        result = cached_fetch(self.store, self.key, self.provider.capabilities, GeoMetSeeing.validate_capabilities,
            now, self.settings["cache_minutes"]*60, self.settings["max_stale_hours"]*3600)
        self.metadata, self.fetched_at, self.status = result.data, result.fetched_at, result.status
        self.metadata_error = result.error
        if self.metadata:
            age = (now-parse(self.metadata["run"])).total_seconds()/3600
            if age < 0 or age > self.settings["max_run_age_hours"]:
                self.metadata, self.status = None, "unavailable"
                message = "Published RDPS model run is outside the configured age limit"
            else:
                message = f"RDPS run {self.metadata['run']}; hourly seeing through {iso(self.end)}; point queries pending"
        else:
            message = "No usable RDPS seeing metadata"
        if result.error:
            message += f"; {result.error}"
        self.store.health("Canadian seeing", self.status, now, message)
        self.report_source(now, result.error)

    @property
    def start(self):
        return max(parse(self.metadata["start"]), parse(self.metadata["run"])+timedelta(hours=1))

    @property
    def end(self):
        return min(parse(self.metadata["end"]), parse(self.metadata["run"])+timedelta(hours=84))

    def sample_time(self, when):
        if not self.metadata or not self.start <= when <= self.end:
            return None
        run = parse(self.metadata["run"])
        hour = math.floor((when-run).total_seconds()/3600 + .5)
        return run + timedelta(hours=hour)

    def prepare(self, times: list[datetime]):
        if not self.metadata:
            return
        if not self.deadline:
            self.deadline = time.monotonic()+30  # total point-acquisition budget per cycle
        run = parse(self.metadata["run"])
        for valid in sorted({v for t in times if (v := self.sample_time(t)) is not None}):
            if iso(valid) in self.samples:
                continue
            key = "eccc-point/" + fingerprint({"v": 1, "observer": self.observer.key, "run": iso(run),
                "valid": iso(valid), "distance": self.settings["max_grid_distance_km"]})
            def fetch():
                gate = self.store.cache(self.gate)
                if (time.monotonic() >= self.deadline or self.requests >= 32 or
                        (gate and gate["next_attempt"] and parse(gate["next_attempt"]) > self.now)):
                    raise RuntimeError("Seeing acquisition deferred")
                self.requests += 1
                return self.provider.point(self.observer, run, valid, self.settings["max_grid_distance_km"])
            def validate(data):
                GeoMetSeeing.validate_point(data, self.observer, run, valid, self.settings["max_grid_distance_km"])
            result = cached_fetch(self.store, key, fetch, validate, self.now, 96*3600, 96*3600)
            self.samples[iso(valid)] = result
            if result.error and result.error.startswith(("HTTP", "Connect", "Read", "Write", "Pool", "RemoteProtocol")):
                failed = self.store.cache(key)
                self.store.cache_failure(self.gate, failed["failures"], parse(failed["next_attempt"]), result.error)
        failures = sum(r.data is None for r in self.samples.values())
        missing = sum(r.data is not None and r.data["value"] is None for r in self.samples.values())
        status = "degraded" if failures or missing else self.status
        self.store.health("Canadian seeing", status, self.now,
            f"RDPS run {iso(run)}; {len(self.samples)} requested hours cached; {failures} failed/deferred; {missing} no-data; {self.requests} HTTP point requests")
        self.report_source(self.now, "Point data incomplete" if failures or missing else None)

    def at(self, when, geometry):
        valid = self.sample_time(when)
        result = self.samples.get(iso(valid)) if valid else None
        notes = []
        if not valid or result is None or result.data is None:
            status = "outside_forecast" if self.metadata and not valid else self.status if not self.metadata else "unavailable"
            return Conditions({}, status, None, None, ["Canadian seeing unavailable for this time; wind suitability is only a fallback proxy"])
        value, grid = result.data["value"], result.data["grid"]
        run = parse(result.data["run"])
        source = Source("Environment and Climate Change Canada", GeoMetSeeing.documentation, iso(result.fetched_at),
            "RDPS 10 km native seeing index via MSC GeoMet; ECCC Data Services End-use Licence")
        if value is None:
            return Conditions({}, "unavailable", iso(result.fetched_at), source, ["RDPS has no seeing value at this location/time"])
        status = "stale" if self.status == "stale" or result.status == "stale" else "fresh"
        lead = max(0, (valid-run).total_seconds()/3600)
        confidence = max(.4, .75-lead*.0035) * (.35 if status == "stale" else 1)
        distance = distance_km(self.observer.latitude, self.observer.longitude, grid[1], grid[0])
        method = (f"RDPS native category ({LABELS[int(value)]}); nearest hourly sample, no interpolation; "
                  f"grid {grid[1]:.4f}, {grid[0]:.4f}, {distance:.1f} km from observer")
        metric = Metric(value, "index (0 masked; 1–5)", round(confidence, 3), "ECCC RDPS", method, iso(valid),
                        status=status, issued_at=iso(run), retrieved_at=iso(result.fetched_at),
                        valid_until=iso(run+timedelta(hours=self.settings["max_run_age_hours"])))
        notes += ["Canadian seeing categories describe modeled atmospheric turbulence, not measured arcseconds",
                  "Cloud masking (index 0) does not establish turbulence quality; no wind-proxy substitution",
                  "Grid-scale forecast does not resolve local ground/telescope turbulence or thermal equilibration"]
        return Conditions({"seeing_index": metric}, status, iso(result.fetched_at), source, notes)
