"""Optional aircraft metadata: legacy licensed files or the public CC0 catalog."""
from datetime import timedelta
from pathlib import Path
import json
import re
from urllib.parse import urlparse

from .adsb import text
from .domain import bounded, iso, parse
from .providers import cached_fetch
from .source_health import FreshnessPolicy, SourceHealth
from .storage import fingerprint


class LocalEnrichment:
    defaults = {"enabled":False,"path":"enrichment.json","cache_seconds":3600,"max_age_hours":24}
    name = "aircraft.enrichment"

    @staticmethod
    def validate_settings(s):
        if not isinstance(s["enabled"],bool) or not isinstance(s["path"],str) or not s["path"]:
            raise ValueError("Enrichment needs boolean enabled and a file path")
        bounded(s["cache_seconds"],300,86400,"enrichment.cache_seconds")
        bounded(s["max_age_hours"],1,168,"enrichment.max_age_hours")

    def __init__(self,client,store,observer,settings,consumers):
        self.store,self.settings,self.consumers = store,settings,consumers
        self.key = "aircraft-enrichment/v1/"+fingerprint({"path":settings["path"]})
        self._frame = ()
        self.health = None

    @staticmethod
    def validate(data):
        if not isinstance(data,list) or len(data) > 1000:
            raise ValueError("Enrichment must be a bounded record list")
        keys = set()
        for row in data:
            if not isinstance(row,dict) or set(row)-{"icao24","callsign","airline","type_name","manufacturer","base_model","origin","destination",
                    "updated_at","valid_until","source_name","source_url","rights"}:
                raise ValueError("Unknown enrichment fields")
            for field in ("icao24","updated_at","valid_until","source_name","source_url","rights"):
                if not text(row.get(field),512):
                    raise ValueError("Enrichment provenance and rights required")
            if not re.fullmatch("[0-9a-f]{6}",row["icao24"]):
                raise ValueError("Invalid enrichment aircraft")
            source = urlparse(row["source_url"])
            if source.scheme not in {"http","https"} or not source.hostname or source.username:
                raise ValueError("Invalid enrichment source URL")
            if not parse(row["updated_at"]) < parse(row["valid_until"]) <= parse(row["updated_at"])+timedelta(days=7):
                raise ValueError("Enrichment validity must be positive and at most 7 days")
            for key in ("callsign","airline","type_name","manufacturer","base_model","origin","destination"):
                text(row.get(key),128)
            if bool(text(row.get("manufacturer"),128)) != bool(text(row.get("base_model"),128)):
                raise ValueError("Manufacturer and base model must be supplied together")
            if text(row.get("airline"),128) and not text(row.get("callsign"),128):
                raise ValueError("Airline needs a bound aircraft and callsign")
            route = row.get("origin") or row.get("destination")
            if route and not (row.get("callsign") and row.get("origin") and row.get("destination")):
                raise ValueError("Routes need bound aircraft, callsign and both endpoints")
            key = row["icao24"],row.get("callsign")
            if key in keys:
                raise ValueError("Duplicate enrichment key")
            keys.add(key)

    def fetch(self):
        with Path(self.settings["path"]).open("rb") as file:
            raw = file.read(256001)
        if len(raw) > 256000:
            raise ValueError("Oversized enrichment file")
        return json.loads(raw)

    def refresh(self,now):
        # All failures contained here; enrichment must never fail its position consumer.
        policy = FreshnessPolicy(self.settings["cache_seconds"],self.settings["max_age_hours"]*3600,
                                 self.settings["max_age_hours"]*3600)
        if not self.settings["enabled"]:
            self._frame = ()
            self.health = SourceHealth(self.name,"catalog",iso(now),policy,self.consumers,
                available=False,details={"disabled":True})
        else:
            result = cached_fetch(self.store,self.key,self.fetch,self.validate,now,
                                  policy.cadence_seconds,policy.invalid_after_seconds)
            self._frame = tuple(result.data or ())
            cache = self.store.cache(self.key)
            self.health = SourceHealth(self.name,"catalog",iso(now),policy,self.consumers,
                retrieved_at=iso(result.fetched_at) if result.fetched_at else None,
                data_at=min((row["updated_at"] for row in self._frame),default=None),
                valid_until=min((row["valid_until"] for row in self._frame),default=None),
                next_refresh=cache["next_attempt"] if cache and cache["next_attempt"] else
                    iso(result.fetched_at+timedelta(seconds=policy.cadence_seconds)) if result.fetched_at else None,
                cached=result.fetched_at != now,error=result.error,available=bool(self._frame),
                details={"method":"User-supplied records with declared rights; no heading-derived route", "non_gating":True})
        self.health.record(self.store)

    def at(self,state,now):
        for row in self._frame:
            if row["icao24"] == state.icao24 and (not row.get("callsign") or row["callsign"] == state.callsign):
                age = (now-parse(row["updated_at"])).total_seconds()
                if age < 0 or age > self.settings["max_age_hours"]*3600 or now > parse(row["valid_until"]):
                    continue
                return {**row,"age_seconds":round(age,1),"retrieved_at":self.health.retrieved_at,"status":"fresh",
                        "usable_until":iso(min(parse(row["valid_until"]),parse(row["updated_at"])+timedelta(hours=self.settings["max_age_hours"])))}
        return None


DEFAULTS = {**LocalEnrichment.defaults, "mode": "local", "refresh_days": 7,
            "metadata_max_age_days": 30, "routes_max_age_days": 14}


def validate_settings(settings):
    LocalEnrichment.validate_settings(settings)
    if settings["mode"] not in ("local", "vrs"):
        raise ValueError("Enrichment mode must be local or vrs")
    bounded(settings["refresh_days"], 1, 30, "enrichment.refresh_days")
    bounded(settings["routes_max_age_days"], settings["refresh_days"], 30, "enrichment.routes_max_age_days")
    bounded(settings["metadata_max_age_days"], settings["routes_max_age_days"], 90, "enrichment.metadata_max_age_days")


def build_enrichment(client, store, observer, settings, consumers, positions=None):
    if settings["mode"] == "vrs":
        from .standing_data import StandingDataEnrichment
        return StandingDataEnrichment(client, store, observer, settings, consumers, positions)
    return LocalEnrichment(client, store, observer, settings, consumers)
