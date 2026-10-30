"""Event-specific observing profiles consume the same atmospheric snapshot."""
from dataclasses import dataclass, replace
from math import sin, radians
from typing import Callable

from .domain import Assessment, Candidate, Conditions, Metric, iso, parse


def deadline(*values):
    timestamps = [parse(value) for value in values if value]
    return iso(min(timestamps)) if timestamps else None


def clamp(value: float) -> float:
    return max(0, min(1, value))


def derive(conditions: Conditions) -> Conditions:
    metrics = dict(conditions.metrics)
    visibility, humidity = metrics.get("visibility"), metrics.get("humidity")
    if visibility and humidity:
        transparency = clamp(visibility.value / 30000) * (1 - 0.35 * clamp((humidity.value - 65) / 35))
        metrics["transparency_proxy"] = Metric(round(transparency * 100, 1), "/100",
            min(visibility.confidence, humidity.confidence) * 0.6, "All Sky Alert",
            "Uncalibrated visibility/humidity proxy; no AOD, extinction or smoke profile", visibility.valid_at)
    surface, jet = metrics.get("wind_surface"), metrics.get("wind_250hpa")
    if surface and jet:
        suitability = (1 - clamp(surface.value / 15)) * (1 - 0.65 * clamp(jet.value / 60))
        metrics["seeing_wind_proxy"] = Metric(round(suitability * 100, 1), "/100",
            min(surface.confidence, jet.confidence) * 0.4, "All Sky Alert",
            "Uncalibrated surface/250 hPa wind suitability; NOT physical astronomical seeing", jet.valid_at)
    return replace(conditions, metrics=metrics)


def planetary(event: Candidate, conditions: Conditions) -> Assessment:
    conditions = derive(conditions)
    m = conditions.metrics
    altitude = clamp(sin(radians(event.geometry.get("altitude_deg", 0))))
    clouds, transparency = m.get("cloud_total"), m.get("transparency_proxy")
    seeing = m.get("seeing_arcsec")
    index = m.get("seeing_index")
    proxy = m.get("seeing_wind_proxy")
    seeing_score = (clamp(1 - (seeing.value - 0.5) / 3) if seeing else
                    clamp((index.value - 1) / 4) if index and index.value > 0 else
                    None if index else proxy.value / 100 if proxy else None)
    seeing_name = "seeing suitability" if seeing else "Canadian seeing suitability" if index else "seeing wind proxy"
    components = {"altitude suitability": round(altitude * 100, 1),
                  "cloud clearance": round(100 - clouds.value, 1) if clouds else None,
                  "transparency proxy": transparency.value if transparency else None,
                  seeing_name: round(seeing_score * 100, 1) if seeing_score is not None else None}
    reasons = ["Intrinsic interest reflects the planet or phenomenon; altitude and atmosphere affect observing quality separately"]
    selected = seeing or index
    usable = conditions.status == "fresh" and (selected is None or selected.status == "fresh")
    expires = deadline(conditions.valid_until, selected.valid_until if selected else None)
    if index and not seeing:
        reasons.append("Canadian RDPS seeing index takes precedence over the uncalibrated wind proxy; categories are not arcseconds")
        reasons.append("Category 1–5 maps to 0–100 suitability as an ordinal scoring heuristic, not a probability or telescope resolution")
        if index.value == 0:
            reasons.append("RDPS seeing is masked by cloud (index 0); turbulence quality is unknown even if another cloud model disagrees")
        if index.status != "fresh":
            reasons.append("Canadian seeing metadata is stale; this assessment cannot qualify a routine alert")
    elif not seeing:
        reasons.append("Seeing in arcseconds is unknown; wind proxy cannot establish that fine detail will resolve")
    if clouds is None or transparency is None or seeing_score is None:
        return Assessment(None, 0, components, reasons + ["Insufficient atmospheric data for a quality estimate"], conditions, usable,
                          expires)
    quality = 100 * altitude * (1 - clouds.value / 100) * (0.5 + 0.5 * transparency.value / 100) * (0.4 + 0.6 * seeing_score)
    confidence = min(clouds.confidence, m["visibility"].confidence) * (0.8 if seeing else 0.6)
    if selected:
        confidence = min(clouds.confidence, m["visibility"].confidence, selected.confidence) * .8
    return Assessment(round(quality, 1), round(confidence, 3), components, reasons, conditions, usable,
                      expires)


def sunset(event: Candidate, conditions: Conditions) -> Assessment:
    conditions = derive(conditions)
    m = conditions.metrics
    low, mid, high = m.get("cloud_low"), m.get("cloud_mid"), m.get("cloud_high")
    transparency = m.get("transparency_proxy")
    if not all((low, mid, high, transparency)):
        return Assessment(None, 0, {}, ["Cloud-layer forecast unavailable; color potential unknown"], conditions,
                          conditions.status == "fresh", conditions.valid_until)
    cloud_canvas = max(mid.value, high.value) / 100
    broken_cloud = clamp(1 - abs(cloud_canvas - 0.5) / 0.5)
    clearance = 1 - low.value / 100
    quality = 100 * clearance * (0.2 + 0.8 * broken_cloud) * (0.5 + 0.5 * transparency.value / 100)
    components = {"low-cloud clearance": round(clearance * 100, 1),
                  "broken mid/high cloud potential": round(broken_cloud * 100, 1),
                  "transparency proxy": transparency.value}
    return Assessment(round(quality, 1), round(min(low.confidence, mid.confidence, high.confidence) * 0.5, 3), components,
        ["Mid/high clouds can help color; low clouds can obscure it", "Experimental heuristic, not a calibrated probability of a brilliant sunset", "No horizon cloud path or aerosol optical depth available"], conditions,
        conditions.status == "fresh", conditions.valid_until)


@dataclass(frozen=True)
class Profile:
    evaluate: Callable[[Candidate, Conditions], Assessment]
    # Optional on injected profiles; allows acquisition only for actual consumers.
    required_metrics: tuple[str, ...] = ()

    def __call__(self, event, conditions):
        return self.evaluate(event, conditions)


PROFILES = {"planetary": Profile(planetary, ("cloud_total", "visibility", "humidity", "seeing_index", "wind_surface", "wind_250hpa")),
            "sunset": Profile(sunset, ("cloud_low", "cloud_mid", "cloud_high", "visibility", "humidity"))}


def eclipse(event, conditions):
    conditions = derive(conditions)
    cloud = conditions.metrics.get("cloud_total")
    transparency = conditions.metrics.get("transparency_proxy")
    altitude = clamp(sin(radians(event.geometry.get("altitude_deg", 0))))
    components = {"altitude suitability":round(altitude*100,1),
                  "cloud clearance":100-cloud.value if cloud else None}
    reasons = ["Eclipse importance is independent of observing conditions; inspect visible phases and local contacts",
               "Cloud fraction is a grid proxy; lunar/solar limb topography and local horizon obstacles are not modeled"]
    if cloud is None or (event.metadata["body"] == "Moon" and transparency is None):
        return Assessment(None, 0, components, reasons+["Weather outside forecast or unavailable"], conditions,
                          conditions.status == "fresh", conditions.valid_until)
    quality = 100*(1-cloud.value/100)*(.5+.5*altitude)
    confidence = cloud.confidence*.8
    if event.metadata["body"] == "Moon":
        quality *= .5+.5*transparency.value/100
        components["transparency proxy"] = transparency.value
        confidence *= .8
    return Assessment(round(quality,1), round(confidence,3), components, reasons, conditions,
                      conditions.status == "fresh", conditions.valid_until)


def aurora(event, conditions):
    conditions = derive(conditions)
    cloud, transparency = conditions.metrics.get("cloud_total"), conditions.metrics.get("transparency_proxy")
    m = event.metadata
    darkness = clamp((-m["sun_altitude_deg"]-6)/12)
    moon = 1-.4*m["moon_illumination"] if m["moon_altitude_deg"] > 0 else 1
    components = {"oval / horizon opportunity":m["horizon_strength"], "darkness":round(darkness*100,1),
                  "Moon suitability":round(moon*100,1), "cloud clearance":100-cloud.value if cloud else None,
                  "transparency proxy":transparency.value if transparency else None}
    reasons = [m["opportunity_band"], "Heuristic observer opportunity score, not a calibrated probability of seeing aurora",
               "Cloud obstruction uses the shared observer grid; directional horizon clouds are unavailable",
               "Model brightness and uncertain emission height do not resolve red/green emission, horizon extinction or local light pollution"]
    source_usable = m["source_status"] in {"healthy", "degraded"}
    usable = source_usable and conditions.status == "fresh"
    if not source_usable:
        reasons.append("OVATION is stale; a routine alert cannot qualify")
    if cloud is None or transparency is None:
        return Assessment(None, 0, components, reasons+["Weather/transparency unavailable"], conditions, usable, conditions.valid_until)
    quality = m["horizon_strength"]*darkness*moon*(1-cloud.value/100)*(.5+.5*transparency.value/100)
    confidence = min(event.occurrence_confidence, cloud.confidence)*(.7 if source_usable else .3)
    return Assessment(round(quality,1), round(confidence,3), components, reasons, conditions, usable, conditions.valid_until)


PROFILES.update(eclipse=Profile(eclipse, ("cloud_total", "visibility", "humidity")),
                aurora=Profile(aurora, ("cloud_total", "visibility", "humidity")))


def satellite(event, conditions):
    conditions = derive(conditions)
    clouds = conditions.metrics.get("cloud_total")
    transparency = conditions.metrics.get("transparency_proxy")
    parts = {"cloud clearance":100-clouds.value if clouds else None,
             "transparency proxy":transparency.value if transparency else None}
    reasons = ["Sunlit/dark-sky orbital geometry establishes the opportunity; cloud and transparency assess observing quality separately.",
               "No calibrated magnitude or seeing requirement is assumed."]
    quality = (100-clouds.value)*(.5+.5*transparency.value/100) if clouds and transparency else None
    confidence = min(clouds.confidence,transparency.confidence) if clouds and transparency else 0
    return Assessment(round(quality,1) if quality is not None else None,confidence,parts,reasons,conditions,
                      conditions.status == "fresh",conditions.valid_until)


def transit(event, conditions):
    clouds = conditions.metrics.get("cloud_total")
    return Assessment(round(100-clouds.value,1) if clouds else None,clouds.confidence if clouds else 0,
        {"cloud clearance":100-clouds.value if clouds else None},
        ["Disk intersection exists independently of weather and darkness; cloud screening estimates observing quality.",
         "Seeing is not a gating input for this short silhouette crossing; timing and orbital uncertainty dominate."] ,
        conditions,conditions.status == "fresh",conditions.valid_until)


def aircraft(event, conditions):
    # This is sky-position relevance, not a calibrated visual-weather score.
    quality = min(100,max(55,event.geometry["altitude_deg"]))
    return Assessment(quality,.8,{"sky position relevance":quality},
        ["Weather does not gate a fresh aircraft track; geometric presence does not confirm visual detection.",
         "Cloud data, if available, are supplementary. Position age gates both UI claims and alerts."],
        conditions,True,None)


PROFILES.update(satellite=Profile(satellite,("cloud_total","visibility","humidity")),
                transit=Profile(transit,("cloud_total",)),aircraft=Profile(aircraft,()))


def crystals(event,conditions):
    # Reassess foreground weather every slow cycle; prediction metadata preserves
    # acquisition-time crystal evidence, not a claimed observation.
    opportunity=event.metadata['opportunity']
    low=conditions.metrics.get('cloud_low')
    quality=opportunity['crystal_suitability']*(1-low.value/100) if low and event.metadata['phenomenon']!='diamond_dust' else opportunity['score'] if conditions.status=='fresh' else None
    return Assessment(round(quality/5)*5 if quality is not None else None,opportunity['confidence'],
        {'geometric eligibility at peak':100,'crystal suitability':opportunity['crystal_suitability'],
         'foreground cloud obstruction':0 if event.metadata['phenomenon']=='diamond_dust' else low.value if low else None,
         'crystal orientation':None},
        [opportunity['method'],'Geometry is eligible at the sampled peak; crystal orientation and habit remain unknown.',
         'Coarse uncalibrated score; local ice/crystal observations and optical depth unavailable'],
        conditions,conditions.status=='fresh',deadline(conditions.valid_until,event.valid_until))


PROFILES['crystals']=Profile(crystals,('cloud_low','cloud_high','temperature_surface','humidity','wind_surface'))
