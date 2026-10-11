"""Two real detectors: pure candidate production, no atmosphere or delivery I/O."""
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .domain import Candidate, DetectionBatch, DetectionContext, Source, bounded, event_id, iso


def dates(context: DetectionContext):
    first = context.now.astimezone(ZoneInfo(context.observer.timezone)).date() - timedelta(days=1)
    # Include previous local date for active windows/recent history, plus full final day.
    return [first + timedelta(days=i) for i in range(context.days + 2)]


def local_time(day, hour: int, context: DetectionContext) -> datetime:
    return datetime.combine(day, time(hour), ZoneInfo(context.observer.timezone)).astimezone(UTC)


def sources(context: DetectionContext) -> list[Source]:
    return [Source("Astronomy Engine", "https://github.com/cosinekitty/astronomy", iso(context.now),
                   f"Local ephemeris version {getattr(context.astronomy, 'version', 'injected')}; ideal horizon with standard refraction")]


class PlanetaryDetector:
    name = "planetary"
    defaults = {"enabled": True, "interval_seconds":3600, "bodies": ["Jupiter", "Saturn", "Mars"],
                "minimum_altitude_deg": 25, "maximum_sun_altitude_deg": -6, "aperture_mm": 203.2}

    @staticmethod
    def validate_settings(settings: dict) -> None:
        bounded(settings["minimum_altitude_deg"], 0, 89, "minimum_altitude_deg")
        bounded(settings["maximum_sun_altitude_deg"], -18, 0, "maximum_sun_altitude_deg")
        bounded(settings["aperture_mm"], 10, 3000, "aperture_mm")
        if not isinstance(settings["bodies"], list) or not settings["bodies"] or any(
            not isinstance(body, str) or body not in {"Jupiter", "Saturn", "Mars"} for body in settings["bodies"]):
            raise ValueError("MVP bodies: Jupiter, Saturn, Mars")
        if len(set(settings["bodies"])) != len(settings["bodies"]):
            raise ValueError("Duplicate planetary body")

    def __init__(self, settings: dict):
        self.settings = settings

    def detect(self, context: DetectionContext) -> DetectionBatch:
        candidates = []
        scopes = set()
        step = timedelta(minutes=15)
        for day in dates(context):
            scope = day.isoformat()
            scopes.add(scope)
            start, end = local_time(day, 12, context), local_time(day + timedelta(days=1), 12, context)
            dark_samples = []
            cursor = start
            while cursor < end:
                sun = context.astronomy.position("Sun", cursor, context.observer)
                if sun["altitude_deg"] <= self.settings["maximum_sun_altitude_deg"]:
                    dark_samples.append(cursor)
                cursor += step
            for body in self.settings["bodies"]:
                eligible = []
                for when in dark_samples:
                    pos = context.astronomy.position(body, when, context.observer)
                    if pos["altitude_deg"] >= self.settings["minimum_altitude_deg"]:
                        eligible.append((when, pos))
                if not eligible:
                    continue
                best = max(range(len(eligible)), key=lambda i: eligible[i][1]["altitude_deg"])
                lo = hi = best
                while lo > 0 and eligible[lo][0] - eligible[lo - 1][0] == step:
                    lo -= 1
                while hi + 1 < len(eligible) and eligible[hi + 1][0] - eligible[hi][0] == step:
                    hi += 1
                peak, position = eligible[best]
                geometry = {k: round(v, 2) for k, v in position.items() if k != "diameter_arcsec"}
                disk = round(position["diameter_arcsec"], 2)
                scale = {"Jupiter": 50, "Saturn": 20, "Mars": 25}[body]
                interest = round(min(88, 15 + 70 * disk / scale), 1)
                diffraction = round(138.4 / self.settings["aperture_mm"], 2)
                candidates.append(Candidate(
                    id=event_id(self.name, context.observer, f"{body}/{scope}"), detector=self.name,
                    kind="planetary.window", observer_key=context.observer.key, scope=scope,
                    title=f"{body}: detail observing window", start=eligible[lo][0], peak=peak,
                    end=min(end, eligible[hi][0] + step), interest=interest,
                    occurrence_confidence=0.98, profile="planetary",
                    temporal_kind="opportunity",
                    reasons=[f"Apparent equatorial disk {disk:.1f} arcsec; peak altitude {geometry['altitude_deg']:.1f}°",
                             f"Sun below {self.settings['maximum_sun_altitude_deg']}°; telescope diffraction reference {diffraction:.2f} arcsec",
                             "Fine detail also depends on seeing, cooling, collimation and magnification"],
                    sources=sources(context) + [Source("NASA planetary facts", "https://nssdc.gsfc.nasa.gov/planetary/factsheet/", iso(context.now), "Equatorial diameter constants; Saturn disk excludes rings")],
                    geometry=geometry, metadata={"version": 1, "body": body, "diameter_arcsec": disk,
                        "aperture_mm": self.settings["aperture_mm"], "diffraction_arcsec": diffraction,
                        "timing_resolution_minutes": 15, "observing_night": scope},
                ))
        return DetectionBatch(candidates, scopes)


class SunsetDetector:
    name = "sunset"
    defaults = {"enabled": True, "interval_seconds":86400}

    def detect(self, context: DetectionContext) -> DetectionBatch:
        candidates, scopes = [], set()
        for day in dates(context):
            scope = day.isoformat()
            scopes.add(scope)
            start, end = local_time(day, 0, context), local_time(day + timedelta(days=1), 0, context)
            sunset = context.astronomy.sunset(start, end, context.observer)
            if sunset is None:  # Polar day/night: absence is a normal result.
                continue
            position = context.astronomy.position("Sun", sunset, context.observer)
            candidates.append(Candidate(
                id=event_id(self.name, context.observer, scope), detector=self.name, kind="sunset.opportunity",
                observer_key=context.observer.key, scope=scope, title="Sunset: cloud-color opportunity",
                start=sunset - timedelta(minutes=20), peak=sunset, end=sunset + timedelta(minutes=20),
                interest=40, occurrence_confidence=0.99, profile="sunset",
                temporal_kind="opportunity",
                reasons=["Calculated local sunset; broken mid/high cloud can catch warm light",
                         "Color potential is experimental: distant western clouds and aerosols are not modeled"],
                sources=sources(context), geometry={k: round(v, 2) for k, v in position.items()},
                metadata={"version": 1, "experimental": True},
            ))
        return DetectionBatch(candidates, scopes)
