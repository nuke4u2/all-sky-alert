"""OVATION cell-to-observer horizon geometry. Heuristic opportunities, not Kp maps."""
from datetime import timedelta
from math import atan2, cos, degrees, radians, sin, sqrt
from zoneinfo import ZoneInfo

from .domain import Candidate, DetectionBatch, Source, bounded, event_id, iso, parse

RADIUS_KM = 6371.0


def cell_geometry(observer, latitude, longitude, height_km):
    """Spherical Earth line of sight from observer to an uncertain emitting layer."""
    lat, target, delta = radians(observer.latitude), radians(latitude), radians(longitude-observer.longitude)
    separation_cos = sin(lat)*sin(target)+cos(lat)*cos(target)*cos(delta)
    separation_cos = max(-1, min(1, separation_cos))
    horizontal = (RADIUS_KM+height_km)*sqrt(max(0, 1-separation_cos**2))
    vertical = (RADIUS_KM+height_km)*separation_cos-(RADIUS_KM+observer.elevation_m/1000)
    elevation = degrees(atan2(vertical, horizontal))
    bearing = degrees(atan2(sin(delta)*cos(target), cos(lat)*sin(target)-sin(lat)*cos(target)*cos(delta)))%360
    return elevation, None if abs(elevation) > 89.99 else bearing


def bearing_label(bearing):
    if bearing is None:
        return "overhead"
    return ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"][int((bearing+22.5)//45)%8]


def opportunity_band(elevation, strength, bearing):
    if elevation <= 5:
        return "Photographic/marginal near horizon"
    if strength < 30:
        return "Photographic/marginal high-sky potential" if elevation >= 60 else f"Photographic/marginal {bearing}-sky potential"
    if elevation < 20:
        return f"Visually plausible low {bearing} horizon"
    if elevation < 60:
        return f"{'Strong' if strength >= 50 else 'Moderate'} {bearing}-sky potential"
    return "High/overhead aurora potential"


class AuroraDetector:
    name = "aurora"
    defaults = {"enabled":False, "interval_seconds":300, "minimum_intensity":10,
                "minimum_horizon_elevation_deg":0, "maximum_sun_altitude_deg":-6,
                "emission_height_min_km":110, "emission_height_max_km":250}

    @staticmethod
    def validate_settings(settings):
        bounded(settings["interval_seconds"], 300, 3600, "aurora.interval_seconds")
        bounded(settings["minimum_intensity"], 1, 100, "aurora.minimum_intensity")
        bounded(settings["minimum_horizon_elevation_deg"], 0, 89, "aurora.minimum_horizon_elevation_deg")
        bounded(settings["maximum_sun_altitude_deg"], -18, 0, "aurora.maximum_sun_altitude_deg")
        bounded(settings["emission_height_min_km"], 80, 200, "aurora.emission_height_min_km")
        bounded(settings["emission_height_max_km"], settings["emission_height_min_km"], 500, "aurora.emission_height_max_km")

    def __init__(self, settings, swpc):
        self.settings, self.swpc = settings, swpc

    def detect(self, context):
        data, health = self.swpc.snapshot(context.now)
        valid = parse(data["Forecast Time"])
        night = (valid.astimezone(ZoneInfo(context.observer.timezone))-timedelta(hours=12)).date().isoformat()
        scopes = {night}
        sun = context.astronomy.position("Sun", valid, context.observer)
        if sun["altitude_deg"] > self.settings["maximum_sun_altitude_deg"]:
            return DetectionBatch([], scopes)
        best = None
        for longitude, latitude, value in data["coordinates"]:
            if value < self.settings["minimum_intensity"]:
                continue
            upper, bearing = cell_geometry(context.observer, latitude, longitude, self.settings["emission_height_max_km"])
            if upper < self.settings["minimum_horizon_elevation_deg"]:
                continue
            lower, _ = cell_geometry(context.observer, latitude, longitude, self.settings["emission_height_min_km"])
            score = value*min(1, max(0, (upper+5)/25))
            if best is None or score > best[0]:
                best = (score, value, lower, upper, bearing, latitude, longitude)
        if best is None:
            return DetectionBatch([], scopes)
        strength, value, lower, upper, bearing, latitude, longitude = best
        moon = context.astronomy.position("Moon", valid, context.observer)
        moon_fraction = context.astronomy.moon_illumination(valid)
        band = opportunity_band(upper, value, bearing_label(bearing))
        expires = min(parse(health["valid_until"]), parse(health["data_at"])+timedelta(seconds=health["policy"]["stale_after_seconds"]))
        stale = health["status"] == "stale"
        reasons = [f"{band}; emitting-height scenario {lower:.1f}–{upper:.1f}° toward {bearing_label(bearing)}",
                   f"Selected NOAA oval cell {latitude:.0f}°, {longitude:.0f}°: index {value:.0f}/100; not a local visual probability",
                   "Apparent elevation depends on assumed emission height; mountains, low-horizon extinction and local light pollution are not resolved",
                   "No Kp-only visibility mapping; the oval grid drives observer-relative geometry"]
        metadata = {"version":1, "opportunity_band":band, "ovation_intensity":value,
                    "horizon_strength":round(strength, 2), "observation_time":data["Observation Time"],
                    "forecast_time":iso(valid), "source_status":health["status"], "source_valid_until":iso(expires),
                    "sun_altitude_deg":round(sun["altitude_deg"], 2), "moon_altitude_deg":round(moon["altitude_deg"], 2),
                    "moon_illumination":round(moon_fraction, 4), "selected_cell":{"latitude":latitude,"longitude":longitude},
                    "emission_height_km":[self.settings["emission_height_min_km"],self.settings["emission_height_max_km"]]}
        event = Candidate(event_id(self.name, context.observer, night), self.name, "aurora.opportunity",
            context.observer.key, night, f"Aurora: {band.lower()}", parse(health["valid_from"]), valid,
            parse(health["valid_until"]), min(85, 35+value*.5), .35 if stale else .65, "aurora", reasons,
            [Source("NOAA SWPC OVATION", "https://www.spaceweather.gov/products/aurora-30-minute-forecast",
                    health["retrieved_at"], "NOAA geographic intensity-derived proxy and forecast timestamp; independently projected onto uncertain emitting-height layers"),
             Source("Astronomy Engine", "https://github.com/cosinekitty/astronomy", iso(context.now),
                    f"Local Sun/Moon positions and lunar illumination, version {getattr(context.astronomy,'version','injected')}")],
            {"azimuth_deg":round(bearing, 1) if bearing is not None else None, "altitude_deg":round(upper, 1),
             "elevation_min_deg":round(lower, 1), "elevation_max_deg":round(upper, 1)}, metadata,
            temporal_kind="transient", valid_until=iso(expires))
        return DetectionBatch([event], scopes)
