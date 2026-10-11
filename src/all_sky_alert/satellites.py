"""Satellite opportunities and nominal ISS disk contacts from shared orbital data."""
from datetime import timedelta

from .domain import Candidate, DetectionBatch, Source, bounded, event_id, iso, parse
from .eclipse import SOLAR_SAFETY
from .orbits import disk_contacts, intervals, minimum


class SatellitePassDetector:
    name = "iss"
    defaults = {"enabled":False, "interval_seconds":300, "horizon_hours":48,
                "catalog_ids":[25544], "minimum_altitude_deg":20, "maximum_sun_altitude_deg":-6}

    @staticmethod
    def validate_settings(s):
        bounded(s["horizon_hours"], 1, 72, "iss.horizon_hours")
        bounded(s["minimum_altitude_deg"], 0, 85, "iss.minimum_altitude_deg")
        bounded(s["maximum_sun_altitude_deg"], -30, 0, "iss.maximum_sun_altitude_deg")
        if not isinstance(s["catalog_ids"], list) or not 1 <= len(s["catalog_ids"]) <= 10:
            raise ValueError("iss.catalog_ids must contain 1–10 IDs")
        for value in s["catalog_ids"]:
            bounded(value, 1, 999999999, "iss.catalog_id")
            if not isinstance(value, int):
                raise ValueError("iss.catalog_id must be integer")
        if len(set(s["catalog_ids"])) != len(s["catalog_ids"]):
            raise ValueError("Duplicate spacecraft ID")

    def __init__(self, settings, orbits):
        self.settings, self.orbits = settings, orbits

    @property
    def cache_identity(self):
        return self.orbits.cache_identity

    def detect(self, context):
        now, observer = context.now, context.observer
        candidates, scopes = [], set()
        records = self.orbits.records(now)
        for catalog_id in self.settings["catalog_ids"]:
            scope = f"catalog/{catalog_id}"
            if catalog_id not in records:
                continue  # Source failure/invalid epoch must not withdraw old forecasts.
            record = records[catalog_id]
            scopes.add(scope)
            geometry = self.orbits.geometry(record, observer)
            epoch = parse(record.epoch)
            scientific_end = epoch+timedelta(hours=self.orbits.settings["stale_hours"])
            horizon = min(now+timedelta(hours=self.settings["horizon_hours"]),
                          epoch+timedelta(hours=self.orbits.settings["invalid_hours"]))
            for rise, full_peak, setting in geometry.passes(now, horizon, 0):
                # Visibility is a conjunction evaluated along the pass, not just at maximum.
                def eligible(seconds):
                    position = geometry.position(rise+timedelta(seconds=seconds))
                    return position["altitude_deg"] >= self.settings["minimum_altitude_deg"] and position["sunlit"] and \
                        position["sun_altitude_deg"] <= self.settings["maximum_sun_altitude_deg"]
                windows = intervals(eligible, (setting-rise).total_seconds())
                for index, (a,b) in enumerate(windows):
                    if b-a <= .05:
                        continue
                    # Boolean shadow/darkness edges are refined to 0.01s. Select
                    # strictly inside that tolerance so the peak cannot land on
                    # the shadow side of a numerical edge.
                    a,b = a+.02,b-.02
                    start, end = rise+timedelta(seconds=a), rise+timedelta(seconds=b)
                    if end <= now or start >= horizon:
                        continue
                    peak_seconds = minimum(lambda x: -geometry.altitude(rise+timedelta(seconds=x)), a, b, .01)
                    peak = rise+timedelta(seconds=peak_seconds)
                    at = geometry.position(peak)
                    age_hours = (peak-epoch).total_seconds()/3600
                    status = "stale" if age_hours > self.orbits.settings["stale_hours"] else \
                        "warning" if age_hours > self.orbits.settings["warning_hours"] else "fresh"
                    name = "ISS" if catalog_id == 25544 else record.name
                    kind = "iss.pass" if catalog_id == 25544 else "spacecraft.pass"
                    provenance = geometry.provenance()
                    provenance.update(epoch_status=status, target_epoch_age_hours=round(age_hours,2))
                    reasons = [(f"{name} likely visible" if catalog_id == 25544 else f"{name} geometric opportunity; brightness unknown")+
                        f": sunlit, above {self.settings['minimum_altitude_deg']}° while the observer sky is dark.",
                        f"Maximum in the visible segment {at['altitude_deg']:.1f}°; no validated apparent-magnitude model.",
                        "Orbital confidence and cloud/transparency assessment are separate."]
                    if status != "fresh":
                        reasons.append("Orbital epoch is aging; predictions may shift, especially after manoeuvres.")
                    candidates.append(Candidate(event_id(self.name, observer, geometry.occurrence(full_peak)+f"/visible/{index}"),
                        self.name, kind, observer.key, scope, f"{name} visible pass" if catalog_id == 25544 else f"{name} sunlit pass · brightness unknown", start, peak, end,
                        min(85,45+at["altitude_deg"]*.45), .2 if status == "stale" else .65 if status == "warning" else .85,
                        "satellite", reasons, [Source("CelesTrak GP",record.source_url,record.retrieved_at,"OMM / local WGS72 SGP4")],
                        at, {"orbit":provenance, "contacts":[
                            {"phase":phase, "time":iso(time), **geometry.position(time)}
                            for phase,time in (("rise",rise),("maximum",full_peak),("set",setting))],
                            "visible_segment":{"start":iso(start),"peak":iso(peak),"end":iso(end)},
                            "shadow_method":"Skyfield spherical Earth shadow; sunlit segment required",
                            "claim":"Geometric visibility opportunity; brightness and weather can vary"},
                        valid_until=iso(scientific_end)))
        return DetectionBatch(candidates, scopes)


class ISSTransitDetector:
    name = "iss_transits"
    defaults = {"enabled":False, "interval_seconds":300, "horizon_hours":24,
                "bodies":["Sun","Moon"], "minimum_altitude_deg":10}

    @staticmethod
    def validate_settings(s):
        bounded(s["horizon_hours"], 1, 24, "iss_transits.horizon_hours")
        bounded(s["minimum_altitude_deg"], 0, 85, "iss_transits.minimum_altitude_deg")
        bodies = s["bodies"]
        if not isinstance(bodies, list) or not bodies or set(bodies)-{"Sun","Moon"} or len(bodies) != len(set(bodies)):
            raise ValueError("Choose unique Sun/Moon transit bodies")

    def __init__(self, settings, orbits):
        self.settings, self.orbits = settings, orbits

    @property
    def cache_identity(self):
        return self.orbits.cache_identity

    def detect(self, context):
        record = self.orbits.records(context.now).get(25544)
        if not record:
            return DetectionBatch([], set())
        epoch = parse(record.epoch)
        expiry = epoch+timedelta(hours=self.orbits.settings["transit_current_hours"])
        if context.now > expiry:
            return DetectionBatch([], set())  # Retained forecasts age out through scientific expiry.
        geometry = self.orbits.geometry(record, context.observer)
        end = min(context.now+timedelta(hours=self.settings["horizon_hours"]),
                  epoch+timedelta(hours=self.orbits.settings["transit_target_hours"]))
        events = []
        scopes = {f"25544/{body}" for body in self.settings["bodies"]}
        for rise, pass_peak, setting in geometry.passes(context.now, end, self.settings["minimum_altitude_deg"]):
            for body in self.settings["bodies"]:
                def sample(seconds):
                    value = geometry.apparent(rise+timedelta(seconds=seconds), body)
                    return value["separation_deg"], value["radius_deg"]
                for a, mid, b in disk_contacts(sample, (setting-rise).total_seconds()):
                    start, peak, finish = [rise+timedelta(seconds=s) for s in (a,mid,b)]
                    if start <= context.now or finish > end:
                        continue
                    at = geometry.transit_geometry(peak, body)
                    if at["body_altitude_deg"] < self.settings["minimum_altitude_deg"]:
                        continue
                    provenance = {**geometry.provenance(),"current_epoch_age_hours":round((context.now-epoch).total_seconds()/3600,3),
                        "target_epoch_age_hours":round((peak-epoch).total_seconds()/3600,3),
                        "epoch_status":"Within configured precision age gates", "current_limit_hours":self.orbits.settings["transit_current_hours"],
                        "target_limit_hours":self.orbits.settings["transit_target_hours"]}
                    meta = {"body":body, "experimental":True, "orbit":provenance,
                        "duration_seconds":round((finish-start).total_seconds(),4),
                        "contacts":[{"phase":p,"time":iso(t)} for p,t in (("ingress",start),("closest",peak),("egress",finish))],
                        "contact_definition":"Nominal spacecraft center intersects spherical apparent body disk; not silhouette contacts",
                        "solver_tolerance_seconds":.001,
                        "claim":"Experimental nominal transit. Subsecond solver precision is not actual timing accuracy; GP supplies no covariance."}
                    if body == "Sun":
                        meta["safety"] = SOLAR_SAFETY
                    events.append(Candidate(event_id(self.name,context.observer,geometry.occurrence(pass_peak)+"/"+body),
                        self.name,"iss.transit."+body.lower(),context.observer.key,f"25544/{body}",
                        f"ISS {body} transit · experimental",start,peak,finish,95,.6,"transit",
                        [f"Nominal ISS center crosses the apparent {body} disk for {(finish-start).total_seconds():.3f} seconds.",
                         "Recheck fresh orbital elements shortly before observing; manoeuvres can move the track off this location.",
                         meta["claim"]], [Source("CelesTrak GP",record.source_url,record.retrieved_at,"OMM / apparent topocentric SGP4 + DE421")],
                        at,meta,valid_until=iso(expiry)))
        return DetectionBatch(events,scopes)
