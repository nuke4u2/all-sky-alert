"""Local lunar occultations of finite planetary disks; no atmosphere or network I/O."""
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from math import asin, degrees
from zoneinfo import ZoneInfo

import numpy as np
from skyfield.api import wgs84

from .detectors import local_time
from .domain import Candidate, DetectionBatch, Source, bounded, event_id, iso
from .eclipse import SOLAR_SAFETY
from .orbits import asset_info, assets, minimum, root


# Volumetric mean radii, km. Circular silhouettes approximate the solid disks;
# oblateness, Saturn's rings, satellites, lunar mountains and valleys are absent.
# NASA/JPL planetary and satellite physical parameters.
RADII_KM = {"Mercury": 2439.4, "Venus": 6051.8, "Mars": 3389.5,
            "Jupiter": 69911, "Saturn": 58232, "Uranus": 25362, "Neptune": 24622}
MOON_RADIUS_KM = 1737.4
CONTACT_LABELS = {"first_contact": "Disappearance begins", "second_contact": "Disappearance complete",
                  "third_contact": "Reappearance begins", "fourth_contact": "Reappearance complete"}


class LunarOccultationEphemeris:
    """Apparent topocentric DE421 directions, light time, aberration and WGS84 site.

    Uses the existing packaged kernel and built-in timescale, never a downloader.
    Scalar contact searches refine minima rather than requiring an inside-disk
    coarse sample, so a short grazing overlap can survive the search.
    """
    version = "de421-circular-disks/v1"

    def __init__(self):
        self.ts, self.eph = assets()
        self.provenance = asset_info()

    @lru_cache(maxsize=4)
    def _site(self, observer):
        return self.eph["earth"]+wgs84.latlon(observer.latitude, observer.longitude,
                                             elevation_m=observer.elevation_m)

    def _target(self, body):
        return self.eph[body.lower() if body in ("Mercury", "Venus", "Mars") else body.lower()+" barycenter"]

    def scan(self, body, times, observer):
        t = self.ts.from_datetimes(times)
        site = self._site(observer).at(t)
        moon = site.observe(self.eph["moon"]).apparent()
        planet = site.observe(self._target(body)).apparent()
        return (planet.separation_from(moon).degrees,
                np.degrees(np.arcsin(MOON_RADIUS_KM/moon.distance().km)),
                np.degrees(np.arcsin(RADII_KM[body]/planet.distance().km)))

    @lru_cache(maxsize=1024)
    def disk(self, body, when, observer):
        t = self.ts.from_datetime(when)
        site = self._site(observer).at(t)
        moon = site.observe(self.eph["moon"]).apparent()
        planet = site.observe(self._target(body)).apparent()
        return (float(planet.separation_from(moon).degrees),
                degrees(asin(MOON_RADIUS_KM/float(moon.distance().km))),
                degrees(asin(RADII_KM[body]/float(planet.distance().km))))

    def at(self, body, when, observer):
        t = self.ts.from_datetime(when)
        site = self._site(observer).at(t)
        moon = site.observe(self.eph["moon"]).apparent()
        planet = site.observe(self._target(body)).apparent()
        sun = site.observe(self.eph["sun"]).apparent()
        altitude, azimuth, _ = planet.altaz()  # No atmospheric refraction.
        return {"altitude_deg": float(altitude.degrees), "geometric_altitude_deg": float(altitude.degrees),
                "azimuth_deg": float(azimuth.degrees), "moon_altitude_deg": float(moon.altaz()[0].degrees),
                "sun_altitude_deg": float(sun.altaz()[0].degrees),
                "solar_elongation_deg": float(planet.separation_from(sun).degrees)}

    def occultations(self, body, start, end, observer):
        if start-timedelta(hours=12) < datetime(1900, 1, 1, tzinfo=UTC) or end+timedelta(hours=12) >= datetime(2054, 1, 1, tzinfo=UTC):
            raise ValueError("Lunar occultation search outside conservative DE421 coverage (1900–2053)")
        # Fixed UTC grid and padding make full contacts independent of evaluation
        # time, requested horizon, visibility clipping and weather.
        step = 3*3600
        begin = (start.timestamp()//step-2)*step
        finish = (end.timestamp()//step+3)*step
        points = np.arange(begin, finish+step/2, step)
        values = []
        # Bound vectorized Skyfield memory on the Pi, including long horizons.
        for offset in range(0, len(points), 256):
            times = [datetime.fromtimestamp(float(t), UTC) for t in points[offset:offset+256]]
            separation, lunar, planetary = self.scan(body, times, observer)
            values.extend((separation-lunar-planetary).tolist())

        def sample(seconds):
            return self.disk(body, datetime.fromtimestamp(seconds, UTC), observer)

        def outside(seconds):
            separation, lunar, planetary = sample(seconds)
            return separation-lunar-planetary

        def inside(seconds):
            separation, lunar, planetary = sample(seconds)
            return separation-lunar+planetary

        previous = None
        for i in range(1, len(points)-1):
            # Generous two-degree proximity gate on a three-hour grid. Refine
            # nearby bracketed minima even when no coarse sample overlaps.
            if values[i] > 2 or not values[i] <= min(values[i-1], values[i+1]):
                continue
            lo, hi = float(points[i-1]), float(points[i+1])
            closest = minimum(outside, lo, hi, tolerance=.1)
            if outside(closest) >= 0 or (previous is not None and abs(closest-previous) < step):
                continue
            while lo > begin and outside(lo) <= 0:
                lo -= step
            while hi < finish and outside(hi) <= 0:
                hi += step
            if outside(lo) <= 0 or outside(hi) <= 0:
                continue  # Incomplete contacts are not predictions.
            first, fourth = root(outside, lo, closest, .1), root(outside, closest, hi, .1)
            maximum = minimum(lambda x: sample(x)[0], first, fourth, .1)
            maximum_time = datetime.fromtimestamp(maximum, UTC)
            if not start <= maximum_time < end:
                continue
            contacts = [("first_contact", first)]
            deepest = minimum(inside, first, fourth, .1)
            partial = inside(deepest) >= 0
            if not partial:
                contacts += [("second_contact", root(inside, first, deepest, .1)),
                             ("third_contact", root(inside, deepest, fourth, .1))]
            contacts.append(("fourth_contact", fourth))
            previous = closest
            yield {"body": body, "maximum": maximum_time, "partial": partial,
                   "contacts": [{"phase": phase, "time": datetime.fromtimestamp(t, UTC)} for phase, t in contacts],
                   "minimum_separation_deg": sample(maximum)[0]}


def visible_segments(margin, start, end, extra=()):
    """Clip preparation/contact windows to altitude, Sun and elongation limits."""
    duration = (end-start).total_seconds()
    points = {0., duration, *[float(t) for t in np.arange(0, duration, 30)]}
    points.update((t-start).total_seconds() for t in extra if start < t < end)
    points = sorted(points)
    def value(seconds):
        return margin(start+timedelta(seconds=seconds))
    flags = [value(t) >= 0 for t in points]
    edges = [0.] if flags[0] else []
    for a, b, fa, fb in zip(points, points[1:], flags, flags[1:]):
        if fa != fb:
            edges.append(root(value, a, b, .1))
    if flags[-1]:
        edges.append(duration)
    return [(start+timedelta(seconds=a), start+timedelta(seconds=b)) for a, b in zip(edges[::2], edges[1::2]) if b > a]


class LunarOccultationDetector:
    name = "lunar_occultations"
    defaults = {"enabled": False, "interval_seconds": 21600, "horizon_days": 90,
                "bodies": list(RADII_KM), "minimum_altitude_deg": 10,
                "maximum_sun_altitude_deg": -6, "minimum_solar_elongation_deg": 20,
                "preparation_minutes": 10}
    cache_identity = LunarOccultationEphemeris.version

    @staticmethod
    def validate_settings(settings):
        if type(settings["horizon_days"]) is not int or not 1 <= settings["horizon_days"] <= 366:
            raise ValueError("lunar_occultations.horizon_days must be an integer 1–366")
        bodies = settings["bodies"]
        if not isinstance(bodies, list) or not bodies or any(not isinstance(b, str) or b not in RADII_KM for b in bodies) or len(set(bodies)) != len(bodies):
            raise ValueError("lunar_occultations.bodies must be distinct supported planets")
        bounded(settings["interval_seconds"], 300, 604800, "lunar_occultations.interval_seconds")
        bounded(settings["minimum_altitude_deg"], 0, 80, "lunar_occultations.minimum_altitude_deg")
        bounded(settings["maximum_sun_altitude_deg"], -18, 90, "lunar_occultations.maximum_sun_altitude_deg")
        bounded(settings["minimum_solar_elongation_deg"], 15, 180, "lunar_occultations.minimum_solar_elongation_deg")
        bounded(settings["preparation_minutes"], 1, 60, "lunar_occultations.preparation_minutes")

    def __init__(self, settings, ephemeris=None):
        self.settings, self.ephemeris = settings, ephemeris

    def detect(self, context):
        if self.ephemeris is None:
            self.ephemeris = LunarOccultationEphemeris()
        ephemeris = self.ephemeris
        today = context.now.astimezone(ZoneInfo(context.observer.timezone)).date()
        first_day = today-timedelta(days=1)
        count = self.settings["horizon_days"]+2
        start = local_time(first_day, 0, context)
        end = local_time(first_day+timedelta(days=count), 0, context)
        scopes = {f"{body}/{(first_day+timedelta(days=i)).isoformat()}" for body in self.settings["bodies"] for i in range(count)}
        candidates = []
        for body in self.settings["bodies"]:
            for event in ephemeris.occultations(body, start, end, context.observer):
                nominal = {c["phase"]: c["time"] for c in event["contacts"]}
                @lru_cache(maxsize=512)
                def geometry(when):
                    return ephemeris.at(body, when, context.observer)
                def margin(when):
                    g = geometry(when)
                    return min(g["geometric_altitude_deg"]-self.settings["minimum_altitude_deg"],
                               g["moon_altitude_deg"]-self.settings["minimum_altitude_deg"],
                               self.settings["maximum_sun_altitude_deg"]-g["sun_altitude_deg"],
                               g["solar_elongation_deg"]-self.settings["minimum_solar_elongation_deg"])
                annotated = [{"phase": c["phase"], "label": CONTACT_LABELS[c["phase"]], "time": iso(c["time"]),
                              "altitude_deg": round(geometry(c["time"])["geometric_altitude_deg"], 2),
                              "observable": margin(c["time"]) >= 0} for c in event["contacts"]]
                if event["partial"]:
                    annotated[0]["label"], annotated[-1]["label"] = "Partial overlap begins", "Partial overlap ends"
                    transitions = [("partial", nominal["first_contact"], nominal["fourth_contact"], event["maximum"])]
                else:
                    transitions = [("disappearance", nominal["first_contact"], nominal["second_contact"],
                                    nominal["first_contact"]+(nominal["second_contact"]-nominal["first_contact"])/2),
                                   ("reappearance", nominal["third_contact"], nominal["fourth_contact"],
                                    nominal["third_contact"]+(nominal["fourth_contact"]-nominal["third_contact"])/2)]
                scope = f"{body}/{event['maximum'].astimezone(ZoneInfo(context.observer.timezone)).date().isoformat()}"
                for transition, begin, finish, reference in transitions:
                    visible = visible_segments(margin, begin-timedelta(minutes=self.settings["preparation_minutes"]),
                                               finish+timedelta(minutes=2), (begin, reference, finish))
                    visible = [(a, b) for a, b in visible if max(a, begin) < min(b, finish)]
                    if not visible:
                        continue  # Visible preparation alone cannot authorize a contact alert.
                    a, b = max(visible, key=lambda pair: (min(pair[1], finish)-max(pair[0], begin)).total_seconds())
                    peak = max(max(a, begin), min(reference, min(b, finish)))
                    if b <= context.now:
                        continue
                    model = {"version": 1, "body": body, "transition": transition, "partial": event["partial"],
                             "contacts": annotated, "nominal_maximum": iso(event["maximum"]),
                             "transition_start": iso(begin), "transition_end": iso(finish),
                             "visible_contact_start": iso(max(a, begin)), "visible_contact_end": iso(min(b, finish)),
                             "minimum_separation_deg": event["minimum_separation_deg"],
                             "ephemeris": ephemeris.provenance, "solver_tolerance_seconds": .1,
                             "contact_definition": "Apparent topocentric circular disks with mean radii; outer-planet system barycenters approximate planet centers. Lunar limb topography, planetary oblateness and Saturn's rings are not modeled. Numerical tolerance is not physical timing uncertainty."}
                    if self.settings["maximum_sun_altitude_deg"] > 0:
                        model["safety"] = SOLAR_SAFETY
                    label = "partial overlap" if transition == "partial" else transition
                    candidates.append(Candidate(
                        id=event_id(self.name, context.observer, f"{body}/{event['maximum'].date().isoformat()}/{transition}"),
                        detector=self.name, kind=f"lunar_occultation.{transition}", observer_key=context.observer.key,
                        scope=scope, title=f"Moon occults {body}: {label}", start=a, peak=peak, end=b,
                        interest=89, occurrence_confidence=.8 if event["partial"] else .95, profile="planetary",
                        reasons=["Rare observer-local lunar occultation; high intrinsic interest remains independent of weather",
                                 "This alert concerns a visible contact; disappearance and reappearance have separate weather/seeing assessments",
                                 "Planning times use smooth circular limbs; verify precision timings with a specialist occultation ephemeris",
                                 "Nominal partial overlap can change with lunar terrain and shape uncertainties" if event["partial"] else "Finite-disk disappearance/reappearance; the fully hidden interval is not an observing opportunity"],
                        sources=[Source("Skyfield / packaged JPL DE421", "https://rhodesmill.org/skyfield/positions.html", iso(context.now),
                                        f"{ephemeris.version}; {ephemeris.provenance}; WGS84 observer; apparent light-time/aberration-corrected directions; airless horizon"),
                                 Source("NASA/JPL physical parameters", "https://ssd.jpl.nasa.gov/planets/phys_par.html", iso(context.now),
                                        "Planetary volumetric mean radii; Moon mean radius 1737.4 km (https://ssd.jpl.nasa.gov/sats/phys_par/)")],
                        geometry=dict(geometry(peak)), metadata=model))
        return DetectionBatch(candidates, scopes)
