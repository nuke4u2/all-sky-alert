"""Observer-radius ADS-B acquisition and replaceable timestamped track contract."""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Protocol
import json
import re

import httpx

from .domain import bounded, iso, parse
from .providers import cached_fetch
from .source_health import FreshnessPolicy, SourceHealth
from .storage import fingerprint


@dataclass(frozen=True)
class AircraftState:
    icao24: str
    position_at: str
    latitude: float
    longitude: float
    altitude_m: float
    altitude_kind: str
    speed_m_s: float | None
    track_deg: float | None
    vertical_m_s: float | None
    callsign: str | None = None
    type_code: str | None = None
    squawk: str | None = None
    position_kind: str = "adsb"


class AircraftSource(Protocol):
    def refresh(self, now): ...
    def snapshot(self, now) -> tuple[list[AircraftState], dict]: ...
    def episode(self, state, observer, timeout, payload): ...


def text(value, maximum=64):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid aircraft text")
    return value.strip() or None


def number(value, low, high, name):
    if value is None:
        return None
    bounded(value,low,high,name)
    return float(value)


def normalize_readsb(data):
    if not isinstance(data, dict) or not isinstance(data.get("ac"), list) or len(data["ac"]) > 3000:
        raise ValueError("Invalid aircraft radius payload")
    bounded(data["now"],0,4102444800000,"ADSB feed time")
    # readsb/re-api uses milliseconds in 'now', seconds in seen_pos.
    timestamp = data["now"]/1000
    if timestamp < 946684800:
        raise ValueError("Expected feed now in milliseconds")
    feed_time = datetime.fromtimestamp(timestamp,UTC)
    states, seen = [], set()
    for row in data["ac"]:
        if not isinstance(row,dict):
            raise ValueError("Invalid aircraft row")
        icao = row.get("hex","")
        # Non-ICAO/anonymous address, intentionally withheld or missing positions:
        # do not reconstruct, enrich or defeat privacy filtering.
        if not isinstance(icao,str) or not re.fullmatch("[0-9a-fA-F]{6}",icao):
            continue
        icao = icao.lower()
        flags = row.get("dbFlags",0)
        bounded(flags,0,65535,"aircraft.dbFlags")
        if not isinstance(flags,int) or flags & 12 or row.get("type") in {"adsb_other","adsr_other","tisb_other","tisb_trackfile"}:
            continue # PIA/LADD flags and anonymized states are not enriched/reconstructed.
        if icao in seen:
            raise ValueError("Duplicate aircraft address")
        seen.add(icao)
        if row.get("lat") is None or row.get("lon") is None or row.get("seen_pos") is None or row.get("alt_baro") == "ground":
            continue
        latitude = number(row["lat"],-90,90,"aircraft.latitude")
        longitude = number(row["lon"],-180,180,"aircraft.longitude")
        age = number(row["seen_pos"],0,86400,"aircraft.seen_pos")
        geometric = row.get("alt_geom")
        height = geometric if geometric is not None else row.get("alt_baro")
        height = number(height,-2000,100000,"aircraft.altitude_ft")
        if height is None:
            continue
        speed = number(row.get("gs"),0,2000,"aircraft.gs")
        track = number(row.get("track"),0,360,"aircraft.track")
        vertical = number(row.get("geom_rate") if geometric is not None else row.get("baro_rate"),-30000,30000,"aircraft.vertical_rate")
        squawk = text(row.get("squawk"),4)
        if squawk is not None and not re.fullmatch("[0-7]{4}",squawk):
            raise ValueError("Invalid squawk")
        states.append(AircraftState(icao,iso(feed_time-timedelta(seconds=age)),latitude,longitude,height*.3048,
            "geometric" if geometric is not None else "barometric approximation",speed*.514444 if speed is not None else None,
            track,vertical*.3048/60 if vertical is not None else None,text(row.get("flight")),
            text(row.get("t")),squawk,text(row.get("type")) or "unknown"))
    return states, feed_time


class ADSBLol:
    name = "adsb.lol"
    defaults = {"enabled":True,"cache_seconds":15,"radius_nm":60,
                "stale_seconds":30,"invalid_seconds":90}
    url = "https://api.adsb.lol/v2/point"

    @staticmethod
    def validate_settings(s):
        if not isinstance(s["enabled"],bool):
            raise ValueError("adsb.enabled must be boolean")
        bounded(s["cache_seconds"],15,3600,"adsb.cache_seconds")
        bounded(s["radius_nm"],1,100,"adsb.radius_nm")
        bounded(s["stale_seconds"],15,60,"adsb.stale_seconds")
        bounded(s["invalid_seconds"],s["stale_seconds"],300,"adsb.invalid_seconds")

    def __init__(self,client,store,observer,settings,consumers):
        self.client,self.store,self.observer,self.settings,self.consumers = client,store,observer,settings,consumers
        self.key = "adsb-radius/v1/"+fingerprint({"observer":observer.key,"radius":settings["radius_nm"],"provider":self.url})
        self._lock,self._frame = Lock(),None
        self.health = None
        from .tracks import TrackBuffer
        self.tracks = TrackBuffer({'track_minutes':10,'max_aircraft':150,'max_points':80})

    def fetch(self):
        path = f"{self.url}/{self.observer.latitude:.6f}/{self.observer.longitude:.6f}/{self.settings['radius_nm']}"
        with self.client.stream("GET",path,timeout=httpx.Timeout(5,connect=3)) as response:
            response.raise_for_status()
            chunks,size = [],0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 2_000_000:
                    raise ValueError("Oversized ADS-B response")
                chunks.append(chunk)
            return json.loads(b"".join(chunks))

    def refresh(self,now):
        with self._lock:
            policy = FreshnessPolicy(self.settings["cache_seconds"],self.settings["stale_seconds"],self.settings["invalid_seconds"])
            states,feed_time = [],None
            if self.settings["enabled"]:
                result = cached_fetch(self.store,self.key,self.fetch,normalize_readsb,now,self.settings["cache_seconds"],self.settings["invalid_seconds"])
                if result.data is not None:
                    states,feed_time = normalize_readsb(result.data)
                cache = self.store.cache(self.key)
                self.health = SourceHealth(self.name,"observation",iso(now),policy,self.consumers,
                    retrieved_at=iso(result.fetched_at) if result.fetched_at else None,data_at=iso(feed_time) if feed_time else None,
                    next_refresh=cache["next_attempt"] if cache and cache["next_attempt"] else
                        iso(result.fetched_at+timedelta(seconds=policy.cadence_seconds)) if result.fetched_at else None,
                    cached=result.fetched_at != now,error=result.error,available=result.data is not None,
                    details={"position_times":[s.position_at for s in states],"aircraft_count":len(states),
                        "position_fresh_seconds":self.settings["stale_seconds"],
                        "note":"Feed time and individual position time are separate; per-position gates enforced",
                        "license":"ADSB.lol ODbL 1.0; preserve attribution", "source_url":"https://www.adsb.lol/docs/open-data/api/"})
            else:
                self.health = SourceHealth(self.name,"observation",iso(now),policy,self.consumers,
                    available=False,details={"disabled":True})
            self.health.record(self.store)
            self.tracks.update(states,now,self.settings['stale_seconds'])
            self._frame = (tuple(states),self.health)

    def snapshot(self,now):
        if self._frame is None or self._frame[1].view(now)["status"] in {"invalid","unavailable","disabled"}:
            raise RuntimeError("No valid aircraft source")
        return self._frame[0],self._frame[1].view(now)

    def episode(self,state,observer,timeout,payload):
        return self.store.track_episode(observer.key,state.icao24,parse(state.position_at),timeout,payload)
