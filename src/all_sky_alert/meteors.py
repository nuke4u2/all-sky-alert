"""Known annual meteor showers: local geometry only; weather belongs to profiles."""
from dataclasses import dataclass
from datetime import timedelta
from math import radians, sin
from zoneinfo import ZoneInfo

from .detectors import dates, local_time, sources
from .domain import Candidate, DetectionBatch, Source, bounded, event_id, iso


CATALOG_URL = "https://www.imo.net/files/meteor-shower/cal2027.pdf"
CATALOG_VERSION = "IMO-2027-table5/v1"


@dataclass(frozen=True)
class Shower:
    code: str
    name: str
    solar_longitude_deg: float  # Equinox J2000, not equinox of date.
    ra_deg: float
    dec_deg: float
    interest: float  # Editorial intrinsic interest, not an expected meteor rate.


# Nominal annual maxima and peak radiants, IMO 2027 Calendar Table 5.
# Deliberately exclude one-off dust-trail/outburst predictions. Fixed peak
# radiants approximate all three nights; radiant drift is not modeled.
SHOWERS = (
    Shower("QUA", "Quadrantids", 283.15, 230, 49, 85),
    Shower("LYR", "Lyrids", 32.32, 271, 34, 65),
    Shower("ETA", "Eta Aquariids", 45.5, 338, -1, 75),
    Shower("CAP", "Alpha Capricornids", 128, 307, -10, 55),
    Shower("SDA", "Southern Delta Aquariids", 128, 340, -16, 70),
    Shower("PER", "Perseids", 140, 48, 58, 88),
    Shower("DRA", "Draconids", 195.4, 263, 56, 55),
    Shower("ORI", "Orionids", 208, 95, 16, 70),
    Shower("STA", "Southern Taurids", 223, 52, 15, 55),
    Shower("NTA", "Northern Taurids", 230, 58, 22, 55),
    Shower("LEO", "Leonids", 235.27, 152, 22, 70),
    Shower("GEM", "Geminids", 262.2, 112, 33, 88),
    Shower("URS", "Ursids", 270.7, 217, 76, 60),
)


def geometry_suitability(geometry):
    """Uncalibrated radiant-height / moonlight suitability, shared with profile."""
    altitude = max(0, sin(radians(geometry["altitude_deg"])))
    moon = (1-0.65*geometry["moon_illumination"]
            if geometry["moon_altitude_deg"] > 0 else 1)
    return (0.65+0.35*altitude)*moon


class MeteorShowerDetector:
    name = "meteors"
    defaults = {"enabled": False, "interval_seconds": 21600,
                "minimum_altitude_deg": 15, "maximum_sun_altitude_deg": -12}
    cache_identity = CATALOG_VERSION

    @staticmethod
    def validate_settings(settings):
        bounded(settings["minimum_altitude_deg"], 0, 89, "meteors.minimum_altitude_deg")
        bounded(settings["maximum_sun_altitude_deg"], -18, -6, "meteors.maximum_sun_altitude_deg")

    def __init__(self, settings):
        self.settings = settings

    def detect(self, context):
        days = dates(context)
        scopes = {day.isoformat() for day in days}
        timezone = ZoneInfo(context.observer.timezone)
        candidates = []
        step = timedelta(minutes=15)
        # Include adjacent years for New Year reconciliation and timezone edges.
        for year in range(days[0].year-1, days[-1].year+2):
            for shower in SHOWERS:
                maximum = context.astronomy.solar_longitude_peak(year, shower.solar_longitude_deg)
                # Observing dates name noon-to-noon local nights. Use the night
                # containing the nominal maximum and its two neighboring nights.
                central = (maximum.astimezone(timezone)-timedelta(hours=12)).date()
                for offset in (-1, 0, 1):
                    day = central+timedelta(days=offset)
                    if day.isoformat() not in scopes:
                        continue
                    start = local_time(day, 12, context)
                    end = local_time(day+timedelta(days=1), 12, context)
                    runs, run = [], []
                    cursor = start
                    while cursor <= end:
                        sun = context.astronomy.position("Sun", cursor, context.observer)["altitude_deg"]
                        radiant = context.astronomy.radiant_position(shower.ra_deg, shower.dec_deg, cursor, context.observer)
                        if (sun <= self.settings["maximum_sun_altitude_deg"] and
                                radiant["altitude_deg"] >= self.settings["minimum_altitude_deg"]):
                            moon = context.astronomy.position("Moon", cursor, context.observer)["altitude_deg"]
                            geometry = {**radiant, "sun_altitude_deg": sun, "moon_altitude_deg": moon,
                                        "moon_illumination": context.astronomy.moon_illumination(cursor)}
                            run.append((cursor, geometry))
                        else:
                            if len(run) >= 2:
                                runs.append(run)
                            run = []
                        cursor += step
                    if len(run) >= 2:
                        runs.append(run)
                    if not runs:
                        continue
                    # One stable occurrence per shower/local night. Choose the
                    # best contiguous geometric segment, independently of weather.
                    run = max(runs, key=lambda r: max(geometry_suitability(g) for _, g in r))
                    peak, geometry = max(run, key=lambda sample: geometry_suitability(sample[1]))
                    scope = day.isoformat()
                    candidates.append(Candidate(
                        id=event_id(self.name, context.observer, f"{shower.code}/{year}/{scope}"),
                        detector=self.name, kind="meteors.shower", observer_key=context.observer.key,
                        scope=scope, title=f"{shower.name}: peak-night meteor observing",
                        start=run[0][0], peak=peak, end=run[-1][0], interest=shower.interest,
                        occurrence_confidence=0.8, profile="meteors", temporal_kind="opportunity",
                        reasons=[f"Night {offset+2} of 3 around the nominal annual maximum",
                                 "Dark-sky window with radiant above the local ideal horizon; moonlight affects quality",
                                 "Annual planning prediction, not a meteor storm or guaranteed rate; adjacent nights can be weaker",
                                 "Fixed peak radiant; no radiant drift, terrain, light pollution or outburst model"],
                        sources=sources(context)+[Source("International Meteor Organization", CATALOG_URL,
                            iso(context.now), f"Bundled {CATALOG_VERSION}; J2000 solar longitude and peak radiant; numerical maximum resolution 1 minute, observing samples 15 minutes")],
                        geometry=geometry, metadata={"version": 1, "shower_code": shower.code,
                            "shower_name": shower.name, "catalog_version": CATALOG_VERSION,
                            "nominal_maximum_utc": iso(maximum), "solar_longitude_j2000_deg": shower.solar_longitude_deg,
                            "radiant_ra_j2000_deg": shower.ra_deg, "radiant_dec_j2000_deg": shower.dec_deg,
                            "observing_night": scope, "peak_night_offset": offset,
                            "timing_resolution_minutes": 15},
                    ))
        return DetectionBatch(candidates, scopes)
