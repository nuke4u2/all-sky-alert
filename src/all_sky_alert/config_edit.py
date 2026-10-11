"""Fixed operational controls and comment-preserving validated atomic config writes."""
from dataclasses import asdict
from hashlib import sha256
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
import tomllib

import tomlkit

from .config import DEFAULTS, from_dict
from .registry import DETECTORS


def field_definitions():
    fields = {}
    def add(path,label,kind):fields[path] = {"label":label,"kind":kind}
    for name,spec in DETECTORS.items():
        add(f"detectors.{name}.enabled",spec.label or name.replace('_',' ').title(),"bool")
    for path,label in (("atmosphere.enabled","Shared weather"),("atmosphere.canadian.enabled","Canadian seeing"),
            ("providers.adsb.enabled","ADSB.lol positions"),("providers.orbits.enabled","CelesTrak orbits"),
            ("providers.swpc.enabled","NOAA aurora"),("providers.hrrr.enabled","Experimental HRRR microphysics"),
            ("notifications.enabled","Local alert milestones"),("gmail.enabled","Gmail delivery"),
            ("map.enabled","Aircraft map"),("map.tiles_enabled","Online map tiles")):
        add(path,label,"bool")
    for path,label in (("service.recent_hours","Recent history (hours)"),
            ("notifications.minimum_interest","Minimum alert interest"),("notifications.minimum_quality","Minimum observing quality"),
            ("detectors.planetary.minimum_altitude_deg","Planet minimum elevation (°)"),
            ("detectors.adsb.minimum_altitude_deg","Aircraft entry elevation (°)"),("detectors.adsb.exit_altitude_deg","Aircraft exit elevation (°)"),
            ("detectors.adsb.low_altitude_m","Interesting low aircraft height (m)"),("providers.adsb.radius_nm","Acquisition radius (nautical miles)"),
            ("map.radius_km","Map interest radius (km)"),("map.track_minutes","Observed trail retention (minutes)"),
            ("detectors.sky_crystals.minimum_opportunity","Crystal minimum opportunity /100"),
            ("detectors.sky_crystals.horizon_hours","Crystal forecast horizon (hours)"),
            ("detectors.sky_crystals.diamond_temperature_c","Diamond-dust temperature ceiling (°C)")):
        add(path,label,"number")
    add("gmail.event_types","Email event families","types")
    return fields


def get_value(data,path):
    for part in path.split('.'):data = data[part]
    return data


def set_value(data,path,value):
    parts=path.split('.')
    for part in parts[:-1]:
        if part not in data:data[part]=tomlkit.table() if isinstance(data,tomlkit.container.Container) else {}
        data=data[part]
    data[parts[-1]]=value


class ConfigEditor:
    def __init__(self,path):
        self.path=Path(path) if path else None
        self.lock=RLock()

    def read(self):
        if not self.path:raise ValueError("Settings require an explicitly loaded configuration file")
        raw=self.path.read_bytes()
        return from_dict(tomllib.loads(raw.decode('utf-8')),self.path.parent,self.path), sha256(raw).hexdigest(), raw

    def save(self,patch,revision=None,sync=False):
        with self.lock:
            current,stamp,raw=self.read()
            if revision is not None and revision != stamp:raise RuntimeError("Configuration changed; reload Settings before saving")
            if not isinstance(patch,dict):raise ValueError("Expected operational settings object")
            fields=field_definitions()
            if not sync and set(patch)-set(fields):raise ValueError("Unknown or protected setting")
            doc=tomlkit.parse(raw.decode('utf-8'))
            if sync:
                def fill(target,defaults):
                    for key,value in defaults.items():
                        if key not in target:target[key]=value
                        elif isinstance(value,dict):fill(target[key],value)
                fill(doc,DEFAULTS)
            for key,value in patch.items():set_value(doc,key,value)
            rendered=tomlkit.dumps(doc)
            validated=from_dict(tomllib.loads(rendered),self.path.parent,self.path)
            # Reject inappropriate JSON types rather than coercing strings/booleans.
            if not sync:
                for key,value in patch.items():
                    kind=fields[key]['kind']
                    if kind=='bool' and not isinstance(value,bool):raise ValueError("Expected boolean setting")
                    if kind=='number' and (isinstance(value,bool) or not isinstance(value,(int,float))):raise ValueError("Expected numeric setting")
            changed=[key for key in patch if get_value(current.values,key)!=get_value(validated.values,key)]
            temporary=None
            try:
                with NamedTemporaryFile(mode='w',encoding='utf-8',newline='\n',dir=self.path.parent,prefix='.config-',suffix='.tmp',delete=False) as file:
                    temporary=Path(file.name)
                    file.write(rendered);file.flush();os.fsync(file.fileno())
                os.chmod(temporary,self.path.stat().st_mode)
                if sha256(self.path.read_bytes()).hexdigest()!=stamp:raise RuntimeError("Configuration changed during save")
                os.replace(temporary,self.path)
            finally:
                if temporary and temporary.exists():temporary.unlink()
            return validated,changed

    def sync(self):
        return self.save({},sync=True)


def runtime_identity(config):
    from .storage import fingerprint
    return fingerprint({"observer":asdict(config.observer),"values":config.values})
