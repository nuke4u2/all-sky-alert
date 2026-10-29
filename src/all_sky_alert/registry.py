"""Explicit composition registrations shared by configuration and evaluation.

New detector modules own their defaults/validation; add a registration here.
There is no dynamic import of untrusted plugins.
"""
from dataclasses import dataclass
from typing import Callable

from .detectors import PlanetaryDetector, SunsetDetector
from .eclipse import EclipseDetector
from .aurora import AuroraDetector
from .domain import Detector
from .satellites import SatellitePassDetector, ISSTransitDetector
from .aircraft import AircraftDetector
from .crystals import SkyCrystalDetector


@dataclass(frozen=True)
class DetectorRegistration:
    factory: Callable[..., Detector]
    defaults: dict
    validate: Callable[[dict], None] | None = None
    dependencies: tuple[str, ...] = ()
    lane: str = "slow"
    profiles: tuple[str, ...] = ()
    ephemeris: bool = True  # Source-health dependency; opt out for pure observations.
    optional_dependencies: tuple[str, ...] = () # Refreshed/injected, failure cannot gate detection.
    label: str = ""
    conditions: bool = False
    # Name of this detector's calendar-day geometry horizon setting. No summary
    # may extend it or assume weather/live forecasts have long-range validity.
    long_range_horizon: str | None = None


DETECTORS = {
    "planetary": DetectorRegistration(PlanetaryDetector, PlanetaryDetector.defaults, PlanetaryDetector.validate_settings, profiles=("planetary",)),
    "sunset": DetectorRegistration(lambda settings: SunsetDetector(), SunsetDetector.defaults, profiles=("sunset",)),
    "eclipses": DetectorRegistration(EclipseDetector, EclipseDetector.defaults, EclipseDetector.validate_settings, profiles=("eclipse",), long_range_horizon='horizon_days'),
    "aurora": DetectorRegistration(AuroraDetector, AuroraDetector.defaults, AuroraDetector.validate_settings, dependencies=("swpc",), lane="live", profiles=("aurora",)),
    "iss": DetectorRegistration(SatellitePassDetector,SatellitePassDetector.defaults,SatellitePassDetector.validate_settings,dependencies=("orbits",),profiles=("satellite",),ephemeris=False),
    "iss_transits": DetectorRegistration(ISSTransitDetector,ISSTransitDetector.defaults,ISSTransitDetector.validate_settings,dependencies=("orbits",),profiles=("transit",),ephemeris=False),
    "adsb": DetectorRegistration(AircraftDetector,AircraftDetector.defaults,AircraftDetector.validate_settings,dependencies=("adsb",),optional_dependencies=("enrichment",),lane="live",profiles=("aircraft",),ephemeris=False),
}
DETECTORS['sky_crystals']=DetectorRegistration(SkyCrystalDetector,SkyCrystalDetector.defaults,SkyCrystalDetector.validate_settings,
    profiles=('crystals',),optional_dependencies=('hrrr',),conditions=True,label='Atmospheric optics')
from dataclasses import replace
for name,label in {'planetary':'Planetary observing','sunset':'Sunsets','eclipses':'Eclipses','aurora':'Aurora',
                   'iss':'ISS / satellite passes','iss_transits':'ISS Sun/Moon transits','adsb':'Aircraft'}.items():
    DETECTORS[name]=replace(DETECTORS[name],label=label)
