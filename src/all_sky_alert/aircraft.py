"""Observer geometry, bounded motion and persistent live track episodes."""
from dataclasses import asdict
from datetime import timedelta
from math import atan2, cos, degrees, hypot, radians, sin, sqrt

from .domain import Candidate, DetectionBatch, Source, bounded, iso, parse


def ecef(latitude,longitude,height_m):
    lat,lon = radians(latitude),radians(longitude)
    radius = 6378137/sqrt(1-6.69437999014e-3*sin(lat)**2)
    return ((radius+height_m)*cos(lat)*cos(lon),(radius+height_m)*cos(lat)*sin(lon),
            (radius*(1-6.69437999014e-3)+height_m)*sin(lat))


def enu(vector,latitude,longitude):
    lat,lon = radians(latitude),radians(longitude)
    x,y,z = vector
    return (-sin(lon)*x+cos(lon)*y,-sin(lat)*cos(lon)*x-sin(lat)*sin(lon)*y+cos(lat)*z,
            cos(lat)*cos(lon)*x+cos(lat)*sin(lon)*y+sin(lat)*z)


def geometry(vector):
    east,north,up = vector
    horizontal = hypot(east,north)
    return {"azimuth_deg":degrees(atan2(east,north))%360 if horizontal > .01 else None,
            "altitude_deg":degrees(atan2(up,horizontal)),"range_km":sqrt(sum(x*x for x in vector))/1000,
            "horizontal_km":horizontal/1000}


def direction(azimuth):
    return "overhead" if azimuth is None else ["north","northeast","east","southeast","south","southwest","west","northwest"][int((azimuth+22.5)%360//45)]


def motion(state,observer,horizon_seconds,elapsed_seconds=0):
    location = ecef(state.latitude,state.longitude,state.altitude_m)
    origin = ecef(observer.latitude,observer.longitude,observer.elevation_m)
    position = enu(tuple(a-b for a,b in zip(location,origin)),observer.latitude,observer.longitude)
    current = geometry(position)
    result = {**current,"altitude_m":state.altitude_m,"altitude_kind":state.altitude_kind,
              "ground_speed_m_s":state.speed_m_s,"ground_track_deg":state.track_deg,"vertical_rate_m_s":state.vertical_m_s,
              "closest_in_seconds":None,"closest_range_km":None,"closest_altitude_deg":None,
              "from_direction":direction(current["azimuth_deg"]),"toward_direction":None,
              "projection_horizon_seconds":horizon_seconds}
    if state.speed_m_s is None or state.track_deg is None or state.speed_m_s < 1:
        return result
    lat,lon,track = radians(state.latitude),radians(state.longitude),radians(state.track_deg)
    east,north,up = state.speed_m_s*sin(track),state.speed_m_s*cos(track),state.vertical_m_s or 0
    velocity_ecef = (-sin(lon)*east-sin(lat)*cos(lon)*north+cos(lat)*cos(lon)*up,
                     cos(lon)*east-sin(lat)*sin(lon)*north+cos(lat)*sin(lon)*up,
                     cos(lat)*north+sin(lat)*up)
    velocity = enu(velocity_ecef,observer.latitude,observer.longitude)
    # Anchor the short projection at the scientific position timestamp. When a
    # sample is several seconds old, closest approach must not be shifted later
    # by that age, nor its old angular position described as the current estimate.
    position = tuple(p+elapsed_seconds*v for p,v in zip(position,velocity))
    result.update(geometry(position))
    closest = -sum(a*b for a,b in zip(position,velocity))/sum(v*v for v in velocity)
    # Never advertise an unbounded or already-past closest approach.
    if 0 <= closest <= max(0,horizon_seconds-elapsed_seconds):
        projected = geometry(tuple(p+closest*v for p,v in zip(position,velocity)))
        result.update(closest_in_seconds=round(closest,1),closest_range_km=projected["range_km"],
                      closest_altitude_deg=projected["altitude_deg"])
    before = geometry(tuple(p-30*v for p,v in zip(position,velocity)))
    after = geometry(tuple(p+30*v for p,v in zip(position,velocity)))
    result.update(from_direction=direction(before["azimuth_deg"]),toward_direction=direction(after["azimuth_deg"]))
    return result


class AircraftDetector:
    name = "adsb"
    defaults = {"enabled":False,"interval_seconds":15,"minimum_altitude_deg":30,"exit_altitude_deg":20,
                "projection_seconds":180,"absence_timeout_seconds":180,"maximum_range_km":80,
                "low_altitude_m":2000,"interesting_callsigns":[],"interesting_types":[],"interesting_icao24":[],
                "emergency_squawks":True}

    @staticmethod
    def validate_settings(s):
        bounded(s["interval_seconds"],15,300,"adsb.interval_seconds")
        bounded(s["minimum_altitude_deg"],5,85,"adsb.minimum_altitude_deg")
        bounded(s["exit_altitude_deg"],0,s["minimum_altitude_deg"],"adsb.exit_altitude_deg")
        bounded(s["projection_seconds"],10,180,"adsb.projection_seconds")
        bounded(s["absence_timeout_seconds"],60,600,"adsb.absence_timeout_seconds")
        bounded(s["maximum_range_km"],1,185,"adsb.maximum_range_km")
        bounded(s["low_altitude_m"],100,5000,"adsb.low_altitude_m")
        for key in ("interesting_callsigns","interesting_types","interesting_icao24"):
            if not isinstance(s[key],list) or len(s[key]) > 100 or not all(isinstance(v,str) and 0 < len(v) <= 32 for v in s[key]):
                raise ValueError("Aircraft interest lists must be short strings")
        if not isinstance(s["emergency_squawks"],bool):
            raise ValueError("emergency_squawks must be boolean")

    def __init__(self,settings,adsb,enrichment):
        self.settings,self.adsb,self.enrichment = settings,adsb,enrichment

    def detect(self,context):
        states,health = self.adsb.snapshot(context.now)
        candidates = []
        # Absence does not withdraw predictions immediately. They expire by position
        # validity; episode table retains continuity through brief source gaps.
        for state in states:
            observed = parse(state.position_at)
            age = (context.now-observed).total_seconds()
            if not 0 <= age <= self.adsb.settings["stale_seconds"]:
                continue
            at = motion(state,context.observer,self.settings["projection_seconds"],age)
            if at["range_km"] > self.settings["maximum_range_km"] or at["altitude_deg"] < 0:
                continue
            current = at["altitude_deg"] >= self.settings["minimum_altitude_deg"]
            approaching = at["closest_in_seconds"] is not None and at["closest_altitude_deg"] >= self.settings["minimum_altitude_deg"]
            # Update continuity for tracks inside the exit region even below entry.
            if not (current or approaching or at["altitude_deg"] >= self.settings["exit_altitude_deg"]):
                continue
            episode = self.adsb.episode(state,context.observer,self.settings["absence_timeout_seconds"],asdict(state))
            if not (current or approaching):
                continue
            try:
                enriched = self.enrichment.at(state,context.now)
            except Exception:
                enriched = None # Prepared enrichment is optional; its failure is non-gating.
            identity = state.callsign or f"Aircraft {state.icao24.upper()}"
            type_name = (enriched.get("type_name") if enriched else None) or state.type_code
            route = f" from {enriched['origin']} to {enriched['destination']}" if enriched and enriched.get("origin") and enriched.get("destination") else ""
            type_text = f", {type_name}," if type_name else ""
            movement = f" moving across the sky from {at['from_direction']} toward {at['toward_direction']}" if at["toward_direction"] else ""
            if at["from_direction"] == at["toward_direction"]:
                movement = f" moving within the {at['from_direction']} sky"
            state_name = "OVERHEAD" if current else "APPROACHING"
            prose = f"{identity}{type_text}{route} is {'in your sky' if current else 'approaching'} at {state.altitude_m/.3048:,.0f} ft"+(f",{movement}" if movement else "")+"."
            expiry = observed+timedelta(seconds=self.adsb.settings["stale_seconds"])
            start = parse(episode["started_at"]) if current else context.now+timedelta(seconds=at["closest_in_seconds"])
            # Approaching uses a short predicted window but its scientific input
            # expires within seconds unless a new state refreshes the same object.
            end = max(expiry, start+timedelta(seconds=30))
            peak = context.now if current else start
            interest = min(85,35+at["altitude_deg"]*.45)
            reasons = [prose,"Geometric sky position is based on the recorded ADS-B position; visual detectability is not confirmed."]
            if 0 < state.altitude_m-context.observer.elevation_m < self.settings["low_altitude_m"]:
                interest = max(interest,65)
                reasons.append("Low altitude relative to the observer; terrain is not modeled.")
            selected = state.callsign in self.settings["interesting_callsigns"] or state.type_code in self.settings["interesting_types"] or state.icao24 in self.settings["interesting_icao24"]
            if selected:
                interest = max(interest,80)
                reasons.append("Matches your configured aircraft interests.")
            if self.settings["emergency_squawks"] and state.squawk in {"7500","7600","7700"}:
                interest = max(interest,85)
                reasons.append(f"Reported emergency squawk {state.squawk}; unverified feed report, not an official emergency advisory.")
            sources = [Source("ADSB.lol", "https://www.adsb.lol/docs/open-data/api/",health["retrieved_at"],
                              "ODbL 1.0 / readsb seen_pos; WGS84 ENU geometry")]
            if enriched:
                sources.append(Source(enriched["source_name"],enriched["source_url"],enriched["retrieved_at"],"User-licensed non-gating metadata; update "+enriched["updated_at"]))
            candidates.append(Candidate(episode["episode_id"],self.name,"aircraft.episode",context.observer.key,episode["episode_id"],f"{identity} · {state_name.lower()}",
                start,peak,end,round(interest,1),.75 if current else .5,"aircraft",reasons,sources,at,
                {"icao24":state.icao24,"callsign":state.callsign,"type_code":state.type_code,"squawk":state.squawk,
                 "track_state":state_name,"position_at":state.position_at,"position_kind":state.position_kind,
                 "episode_started_at":episode["started_at"],"enrichment":enriched,"route_status":"available" if route else "route unknown",
                 "geometry_at":iso(context.now),"position_age_seconds":round(age,1),
                 "projection_method":"Constant local ECEF velocity, <=180s; turns/climbs/position error unresolved",
                 "prose":prose,"visual_detection":"not confirmed"},temporal_kind="transient",valid_until=iso(expiry)))
        return DetectionBatch(candidates,{event.scope for event in candidates})
