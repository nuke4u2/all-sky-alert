"""Explicit source factories/defaults; concrete consumers determine what starts."""
from dataclasses import dataclass
from typing import Callable

from .swpc import SWPC
from .orbit_data import CelesTrak
from .adsb import ADSBLol
from .enrichment import LocalEnrichment
from .hrrr import HRRR
from pathlib import Path


@dataclass(frozen=True)
class SourceRegistration:
    factory: Callable
    defaults: dict
    validate: Callable
    path_fields: tuple[str, ...] = ()
    health_prefix: str = ""


SOURCES = {"swpc": SourceRegistration(
    lambda client, store, observer, settings, consumers: SWPC(client, store, settings, consumers),
    SWPC.defaults, SWPC.validate_settings)}
SOURCES.update(orbits=SourceRegistration(CelesTrak,CelesTrak.defaults,CelesTrak.validate_settings),
               adsb=SourceRegistration(ADSBLol,ADSBLol.defaults,ADSBLol.validate_settings),
               enrichment=SourceRegistration(LocalEnrichment,LocalEnrichment.defaults,LocalEnrichment.validate_settings,("path",)))
from dataclasses import replace
SOURCES['enrichment']=replace(SOURCES['enrichment'],health_prefix='aircraft.enrichment')
SOURCES['hrrr']=SourceRegistration(HRRR,HRRR.defaults,HRRR.validate_settings,health_prefix='noaa.hrrr')


def build_sources(client, store, observer, settings, consumers, base=None):
    resolved = {name:{**value,**{key:str((Path(base)/value[key]).resolve()) for key in SOURCES[name].path_fields}}
                if base is not None else value for name,value in settings.items()}
    return {name: SOURCES[name].factory(client, store, observer, resolved[name], names)
            for name, names in consumers.items()}
