"""Shared local orbital geometry. No HTTP, mail or hidden asset downloads."""
from datetime import timedelta
from functools import lru_cache
from importlib.metadata import version
from importlib.resources import files
import hashlib
from math import asin, degrees, floor
from pathlib import Path

import numpy as np
from skyfield.api import EarthSatellite, load, load_file, wgs84

from .domain import iso, parse


def minimum(function, left, right, tolerance=.001):
    """Golden-section local minimum on a coarse bracket (seconds)."""
    ratio = (5**.5-1)/2
    x, y = right-ratio*(right-left), left+ratio*(right-left)
    fx, fy = function(x), function(y)
    while right-left > tolerance:
        if fx < fy:
            right, y, fy = y, x, fx
            x = right-ratio*(right-left)
            fx = function(x)
        else:
            left, x, fx = x, y, fy
            y = left+ratio*(right-left)
            fy = function(y)
    return (left+right)/2


def root(function, left, right, tolerance=.001):
    fl, fr = function(left), function(right)
    if fl*fr > 0:
        raise ValueError("Root not bracketed")
    while right-left > tolerance:
        mid = (left+right)/2
        fm = function(mid)
        if fl*fm <= 0:
            right, fr = mid, fm
        else:
            left, fl = mid, fm
    return (left+right)/2


def intervals(function, duration, step=5):
    """True intervals, refining state edges to 0.01s; endpoints are clipped."""
    points = list(np.arange(0, duration, step)) + [duration]
    flags = [bool(function(float(x))) for x in points]
    edges = [0.0] if flags[0] else []
    for a, b, fa, fb in zip(points, points[1:], flags, flags[1:]):
        if fa != fb:
            edges.append(root(lambda x: 1 if function(x) else -1, float(a), float(b), .01))
    if flags[-1]:
        edges.append(duration)
    return list(zip(edges[::2], edges[1::2]))


def disk_contacts(sample, duration, step=5):
    """Search separation minima, not inside-disk samples; catch subsecond crossings.

    sample(seconds) returns separation and disk angular radius in degrees.
    Five-second samples bracket each smooth closest approach, then refine to 1ms.
    No finite spacecraft-size claim: contacts describe its center track only.
    """
    points = list(np.arange(0, duration, step)) + [duration]
    values = [sample(float(x))[0] for x in points]
    found = []
    for i in range(1, len(points)-1):
        if not (values[i] <= values[i-1] and values[i] <= values[i+1]):
            continue
        low, high = float(points[i-1]), float(points[i+1])
        closest = minimum(lambda x: sample(x)[0], low, high)
        boundary = lambda x: sample(x)[0]-sample(x)[1]
        if boundary(closest) >= 0:  # Tangency has no positive center-track duration.
            continue
        # Extend contact bracket if a low-altitude disk crossing exceeds coarse step.
        while low > 0 and boundary(low) < 0:
            low = max(0, low-step)
        while high < duration and boundary(high) < 0:
            high = min(duration, high+step)
        if boundary(low) <= 0 or boundary(high) <= 0:
            continue  # Never report incomplete contacts at a search boundary.
        ingress, egress = root(boundary, low, closest), root(boundary, closest, high)
        if not found or closest-found[-1][1] > step:
            found.append((ingress, closest, egress))
    return found


@lru_cache(maxsize=1)
def assets():
    # load_file cannot download; builtin timescale avoids silently refreshing IERS.
    ts = load.timescale(builtin=True)
    # This packaged file is immutable. Do not load/check the unrelated expiring
    # IERS file shipped beside it; the built-in timescale is deliberately used.
    kernel = Path(str(files("skyfield_data").joinpath("data","de421.bsp")))
    eph = load_file(str(kernel))
    return ts, eph


@lru_cache(maxsize=1)
def asset_info():
    kernel = files("skyfield_data").joinpath("data","de421.bsp")
    return {"library":"Skyfield", "version":version("skyfield"),
            "kernel":"JPL DE421", "kernel_sha256":hashlib.sha256(kernel.read_bytes()).hexdigest(),
            "validity":"Conservative DE421 coverage 1900–2053; built-in leap/TT/UT1 tables, no downloaded EOP"}


class OrbitGeometry:
    def __init__(self, record, observer):
        self.record, self.observer = record, observer
        self.ts, self.eph = assets()
        self.satellite = EarthSatellite.from_omm(self.ts, record.elements)
        self.site = wgs84.latlon(observer.latitude, observer.longitude, elevation_m=observer.elevation_m)
        self.topo = self.satellite-self.site
        self.barycenter_site = self.eph["earth"]+self.site
        self.barycenter_satellite = self.eph["earth"]+self.satellite

    def time(self, when):
        return self.ts.from_datetime(when)

    def position(self, when):
        t = self.time(when)
        p = self.topo.at(t)
        if p.message or not np.all(np.isfinite(p.position.km)):
            raise ValueError("SGP4 propagation error")
        alt, az, distance = p.altaz()  # airless, WGS84 topocentric
        sun = self.barycenter_site.at(t).observe(self.eph["sun"]).apparent()
        rate = float(np.dot(p.position.km, p.velocity.km_per_s)/distance.km)
        return {"altitude_deg":float(alt.degrees), "azimuth_deg":float(az.degrees),
                "range_km":float(distance.km), "range_rate_km_s":rate,
                "sunlit":bool(self.satellite.at(t).is_sunlit(self.eph)),
                "sun_altitude_deg":float(sun.altaz()[0].degrees)}

    def altitude(self, when):
        p = self.topo.at(self.time(when))
        if p.message or not np.all(np.isfinite(p.position.km)):
            raise ValueError("SGP4 propagation error")
        return float(p.altaz()[0].degrees)

    def apparent(self, when, body):
        t = self.time(when)
        observer = self.barycenter_site.at(t)
        target = observer.observe(self.eph[body.lower()]).apparent()
        satellite = observer.observe(self.barycenter_satellite).apparent()
        if getattr(satellite,"message",None) or not np.all(np.isfinite(satellite.position.km)):
            raise ValueError("SGP4 propagation error")
        radius_km = 695700 if body == "Sun" else 1737.4
        radius_deg = degrees(asin(radius_km/target.distance().km))
        altitude, azimuth, _ = target.altaz()
        return {"separation_deg":float(satellite.separation_from(target).degrees),
                "radius_deg":radius_deg, "body_altitude_deg":float(altitude.degrees),
                "body_azimuth_deg":float(azimuth.degrees), "range_km":float(satellite.distance().km),
                "satellite":satellite}

    def transit_geometry(self, when, body):
        p = self.apparent(when, body)
        before = self.apparent(when-timedelta(seconds=.1), body)["satellite"]
        after = self.apparent(when+timedelta(seconds=.1), body)["satellite"]
        ra1, dec1, _ = before.radec()
        ra2, dec2, _ = after.radec()
        delta_ra = (ra2.radians-ra1.radians+np.pi)%(2*np.pi)-np.pi
        east, north = delta_ra*np.cos((dec1.radians+dec2.radians)/2), dec2.radians-dec1.radians
        direction = float(np.degrees(np.arctan2(east, north))%360)
        velocity = float(before.separation_from(after).degrees/.2)
        return {key:value for key,value in p.items() if key != "satellite"} | {
            "position_angle_deg":direction, "angular_speed_deg_s":velocity,
            "altitude_deg":p["body_altitude_deg"], "azimuth_deg":p["body_azimuth_deg"]}

    def passes(self, start, end, minimum_altitude=0):
        # Include a pass already in progress; keep true contacts and clip in consumer.
        t, events = self.satellite.find_events(self.site, self.time(start-timedelta(minutes=30)),
                                              self.time(end+timedelta(minutes=30)), altitude_degrees=minimum_altitude)
        rise, peaks = None, []
        result = []
        for moment, event in zip(t, events):
            when = moment.utc_datetime()
            if event == 0:
                rise, peaks = when, []
            elif event == 1 and rise:
                peaks.append(when)
            elif event == 2 and rise and peaks:
                maximum = max(peaks, key=self.altitude)
                if when > start and rise < end:
                    result.append((rise, maximum, when))
                rise, peaks = None, []
        return result

    def occurrence(self, peak):
        # Revolution estimate uses orbital argument of latitude at epoch, making
        # identity insensitive to refreshed epoch/contact times on the same pass.
        e = self.record.elements
        phase = ((e["MEAN_ANOMALY"]+e["ARG_OF_PERICENTER"])%360)/360
        revolution = e["REV_AT_EPOCH"]+floor((peak-parse(self.record.epoch)).total_seconds()/86400*e["MEAN_MOTION"]+phase)
        return f"{self.record.catalog_id}/{revolution}"

    def provenance(self):
        return {"element_epoch":self.record.epoch, "retrieved_at":self.record.retrieved_at,
                "catalog_id":self.record.catalog_id, "element_identity":self.record.identity,
                "propagator":f"SGP4 {version('sgp4')} / Skyfield {version('skyfield')}",
                "time_system":"UTC input; Skyfield built-in leap/TT/UT1 tables; no live EOP",
                "frames":"WGS72 SGP4 TEME to GCRS; WGS84 observer; airless horizon",
                "ephemeris":"Packaged JPL DE421 (1900–2053); skyfield-data "+version("skyfield-data"),
                "kernel_sha256":asset_info()["kernel_sha256"],
                "uncertainty":{"covariance":"Not supplied by GP", "timing_error_seconds":None,
                               "note":"Age gates are not a calibrated uncertainty; manoeuvres can invalidate fresh GP"}}
