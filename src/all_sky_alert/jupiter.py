"""Observer-local Jupiter phenomena, assessed through shared planetary conditions."""
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .detectors import dates, local_time
from .domain import Candidate, DetectionBatch, Source, bounded, event_id, iso, parse
from .jovian import MOONS, PERIOD_DAYS, JovianEphemeris, intervals

GRS_SOURCE = "https://skyandtelescope.org/stargazing-and-observing/interactive-stargazing-tools/transit-times-of-jupiters-great-red-spot/"
KINDS = ("transit", "shadow_transit", "eclipse", "occultation")
LABELS = {"transit": "disk transit", "shadow_transit": "shadow-center transit",
          "eclipse": "eclipse", "occultation": "occultation", "grs": "Great Red Spot"}
INTEREST = {"transit": 64, "shadow_transit": 70, "eclipse": 62, "occultation": 60, "grs": 62}
CONTACT_NOTE = (
    "Approximate center-based planning times, not finite-moon limb contacts or photometric "
    "disappearance times. Eclipse uses an approximate finite-Sun umbra; shadow timing "
    "tracks its center ray, not the shadow's edge. Begin observing at least 10 minutes "
    "before a contact; selected reference checks differ by up to six minutes, with no "
    "validated global error bound. Grazing events "
    "shorter than the five-minute search spacing can be missed."
)


@dataclass(frozen=True)
class Feature:
    key: str
    moon: str
    kind: str
    start: datetime
    peak: datetime
    end: datetime
    label: str
    nominal_start: datetime
    nominal_end: datetime


class JupiterDetector:
    name = "jupiter"
    defaults = {
        "enabled": False, "interval_seconds": 21600, "horizon_days": 14,
        "minimum_altitude_deg": 30, "maximum_sun_altitude_deg": -6, "aperture_mm": 203.2,
        "phenomena": list(KINDS), "include_overlaps": True,
        "transition_window_minutes": 15,
        "grs_enabled": True, "grs_reference_utc": "2026-06-01T00:00:00Z",
        "grs_longitude_deg": 91.0, "grs_drift_deg_per_month": 1.75,
        "grs_reference_max_age_days": 180, "grs_window_minutes": 50,
        "grs_source_url": GRS_SOURCE,
    }

    @staticmethod
    def validate_settings(settings):
        for key, lo, hi in (
            ("interval_seconds", 300, 604800), ("horizon_days", 1, 31),
            ("minimum_altitude_deg", 0, 89), ("maximum_sun_altitude_deg", -18, 0),
            ("aperture_mm", 10, 3000), ("transition_window_minutes", 5, 60),
            ("grs_longitude_deg", 0, 360), ("grs_drift_deg_per_month", -20, 20),
            ("grs_reference_max_age_days", 1, 366), ("grs_window_minutes", 10, 80),
        ):
            bounded(settings[key], lo, hi, "jupiter."+key)
        for key in ("horizon_days", "grs_reference_max_age_days"):
            if not isinstance(settings[key], int):
                raise ValueError("jupiter."+key+" must be an integer")
        for key in ("include_overlaps", "grs_enabled"):
            if not isinstance(settings[key], bool):
                raise ValueError("jupiter."+key+" must be boolean")
        kinds = settings["phenomena"]
        if (not isinstance(kinds, list) or any(not isinstance(k, str) or k not in KINDS for k in kinds)
                or len(set(kinds)) != len(kinds)):
            raise ValueError("jupiter.phenomena must be unique transit/shadow_transit/eclipse/occultation values")
        if not kinds and not settings["grs_enabled"]:
            raise ValueError("Enable a Jupiter phenomenon or GRS")
        if not isinstance(settings["grs_reference_utc"], str):
            raise ValueError("jupiter.grs_reference_utc must be an aware ISO timestamp")
        parse(settings["grs_reference_utc"])
        from urllib.parse import urlsplit
        url = urlsplit(settings["grs_source_url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("jupiter.grs_source_url must be a public HTTPS reference URL")

    def __init__(self, settings, *, ephemeris=None):
        self.settings = settings
        self.ephemeris = ephemeris if ephemeris is not None else JovianEphemeris()
        self.cache_identity = {"geometry": self.ephemeris.version,
                               "library": getattr(self.ephemeris, "library_version", "injected")}

    def _grs(self, when, state):
        reference = parse(self.settings["grs_reference_utc"])
        longitude = (self.settings["grs_longitude_deg"] +
                     self.settings["grs_drift_deg_per_month"]*(when-reference).total_seconds()/86400/30.4375)
        delta = (state.central_meridian_ii-longitude+180) % 360-180
        return self.settings["grs_window_minutes"]*870.270/1440-abs(delta)

    @staticmethod
    def _key(moon, kind, center):
        # Orbital/rotation counts, independent of weather, clipping or root rounding.
        days = (center-datetime(2000, 1, 1, 12, tzinfo=UTC)).total_seconds()/86400
        period = 360/870.270 if kind == "grs" else PERIOD_DAYS[moon]
        return f"{moon}/{kind}/{round(days/period)}"

    def detect(self, context):
        # Keep work bounded even if the general prediction horizon is very large.
        days = dates(replace(context, days=min(context.days, self.settings["horizon_days"])))
        scopes = {day.isoformat() for day in days}
        begin = local_time(days[0], 0, context)
        finish = local_time(days[-1]+timedelta(days=1), 0, context)
        # Complete nominal events before clipping; fixed UTC grid makes repeated
        # calculations deterministic across evaluation times and DST changes.
        scan_begin = (begin-timedelta(hours=12)).replace(minute=0, second=0, microsecond=0)
        scan_end = finish+timedelta(hours=12)
        times = [scan_begin+timedelta(minutes=5*i) for i in range(int((scan_end-scan_begin).total_seconds()/300)+1)]
        states = [self.ephemeris.at(t, context.observer) for t in times]

        def state(t):
            return self.ephemeris.at(t, context.observer)

        def visibility(t):
            if t < scan_begin or t > scan_end:
                return -1.0
            position = context.astronomy.position("Jupiter", t, context.observer)
            sun = context.astronomy.position("Sun", t, context.observer)
            return min(position["geometric_altitude_deg"]-self.settings["minimum_altitude_deg"],
                       self.settings["maximum_sun_altitude_deg"]-sun["altitude_deg"])

        # Pad visibility with negative edge samples to retain polar-night runs.
        visibility_times = [scan_begin-timedelta(seconds=1), *times, scan_end+timedelta(seconds=1)]
        values = [-1, *[visibility(t) for t in times], -1]
        visible = [(max(a, begin), min(b, finish))
                   for a, b in intervals(visibility_times, values, visibility)
                   if max(a, begin) < min(b, finish)]
        reference = parse(self.settings["grs_reference_utc"])
        grs_expiry = reference+timedelta(days=self.settings["grs_reference_max_age_days"])
        grs_status = ("disabled" if not self.settings["grs_enabled"] else
                      "reference expired" if context.now >= grs_expiry else
                      "reference not yet applicable" if context.now < reference else "available")
        features = []
        for moon in MOONS:
            for kind in self.settings["phenomena"]:
                key = moon, kind
                for a, b in intervals(times, [s.margins[key] for s in states], lambda t: state(t).margins[key]):
                    center = a+(b-a)/2
                    token = self._key(moon, kind, center)
                    if kind in ("transit", "shadow_transit"):
                        features.append(Feature(token, moon, kind, a, center, b,
                                                f"{moon} {LABELS[kind]}", a, b))
                    else:
                        # An eclipse boundary behind Jupiter, or an occultation
                        # boundary while eclipsed, cannot be observed as a change.
                        other = "occultation" if kind == "eclipse" else "eclipse"
                        for phase, t in (("disappearance", a), ("reappearance", b)):
                            if state(t).margins[moon, other] > 0:
                                continue
                            pad = timedelta(minutes=self.settings["transition_window_minutes"])
                            features.append(Feature(token+"/"+phase, moon, kind, t-pad, t, t+pad,
                                                    f"{moon} {kind} {phase}", a, b))
        if grs_status == "available":
            for a, b in intervals(times, [self._grs(t, s) for t, s in zip(times, states)],
                                 lambda t: self._grs(t, state(t))):
                center = a+(b-a)/2
                if reference <= a and b < grs_expiry:
                    features.append(Feature(self._key("Jupiter", "grs", center), "Jupiter", "grs",
                                            a, center, b, LABELS["grs"]+" near central meridian", a, b))

        candidates, local_features = [], []
        for feature in features:
            for a, b in visible:
                lo, hi = max(a, feature.start), min(b, feature.end)
                if (hi-lo).total_seconds() < 30:
                    continue
                # Transition events must include their actual nominal boundary,
                # otherwise a pre/post window alone would imply a visible change.
                if feature.kind in ("eclipse", "occultation") and not lo <= feature.peak <= hi:
                    continue
                local_features.append((lo, hi, feature))
                candidates.append(self._candidate(context, lo, hi, [feature], grs_status, grs_expiry))

        if self.settings["include_overlaps"]:
            continuous = [(a, b, f) for a, b, f in local_features
                          if f.kind not in ("eclipse", "occultation")]
            boundaries = sorted({t for lo, hi, f in continuous for t in (lo, hi)})
            for lo, hi in zip(boundaries, boundaries[1:]):
                if (hi-lo).total_seconds() < 60:
                    continue
                active = [f for a, b, f in continuous if a <= lo and b >= hi]
                active = list({f.key:f for f in active}.values())
                if len(active) < 2:
                    continue
                # The same pair can recur before/after a third feature. Identify
                # that distinct segment by the feature boundary, not its seconds.
                segment = "+".join(sorted(f.key+("/start" if a == lo else "/end")
                                          for a, b, f in continuous if a == lo or b == lo))
                candidates.append(self._candidate(context, lo, hi, active, grs_status, grs_expiry, segment))
            # A transition's preparation window is not a continuously visible
            # phenomenon. Count it only when its boundary itself coincides with
            # another feature; mere overlap of the preparation time is insufficient.
            for a, b, transition in local_features:
                if transition.kind not in ("eclipse", "occultation"):
                    continue
                others = [(lo, hi, f) for lo, hi, f in continuous if lo <= transition.peak <= hi]
                if not others:
                    continue
                lo = max(a, *(lo for lo, hi, f in others))
                hi = min(b, *(hi for lo, hi, f in others))
                if (hi-lo).total_seconds() >= 60:
                    candidates.append(self._candidate(context, lo, hi, [transition]+[f for a,b,f in others],
                                                       grs_status, grs_expiry, transition.key))
        return DetectionBatch(candidates, scopes)

    def _candidate(self, context, lo, hi, features, grs_status, grs_expiry, segment=""):
        combined = len(features) > 1
        # Maximize altitude within the *simultaneous* interval. Transitions retain
        # their nominal boundary as peak so weather is sampled at the useful time.
        transition = [f.peak for f in features if f.kind in ("eclipse", "occultation") and lo <= f.peak <= hi]
        if transition:
            peak = min(transition, key=lambda t: abs((t-(lo+(hi-lo)/2)).total_seconds()))
        elif len(features) == 1 and features[0].kind == "grs":
            peak = max(lo, min(hi, features[0].peak))
        else:
            samples = [lo+(hi-lo)*i/12 for i in range(13)]
            peak = max(samples, key=lambda t: context.astronomy.position("Jupiter", t, context.observer)["geometric_altitude_deg"])
        position = context.astronomy.position("Jupiter", peak, context.observer)
        interest = min(88, max(INTEREST[f.kind] for f in features)+10*(len(features)-1)+
                       (5 if combined and any(f.kind == "grs" for f in features) else 0))
        labels = [f.label for f in features]
        has_grs = any(f.kind == "grs" for f in features)
        metadata = {
            "version": 1, "body": "Jupiter", "diameter_arcsec": round(position["diameter_arcsec"], 2),
            "aperture_mm": self.settings["aperture_mm"], "diffraction_arcsec": round(138.4/self.settings["aperture_mm"], 2),
            "features": [{"name":f.label, "moon":f.moon, "phenomenon":f.kind,
                          "nominal_start":iso(f.nominal_start), "nominal_end":iso(f.nominal_end),
                          "reference_time":iso(f.peak)} for f in features],
            "contact_definition": CONTACT_NOTE, "search_spacing_minutes": 5, "solver_tolerance_seconds": 1,
            "grs_status": grs_status, "grs_reference": {
                "epoch": self.settings["grs_reference_utc"], "longitude_deg": self.settings["grs_longitude_deg"],
                "drift_deg_per_month": self.settings["grs_drift_deg_per_month"], "month_days": 30.4375,
                "valid_until": iso(grs_expiry), "source_url": self.settings["grs_source_url"],
            },
            "ephemeris_version": getattr(self.ephemeris, "library_version", "injected"),
            "visibility_ephemeris_version": getattr(context.astronomy, "version", "injected"),
            "geometry_method": self.ephemeris.version,
        }
        provenance = [Source("Astronomy Engine", "https://github.com/cosinekitty/astronomy", iso(context.now),
                            f"Local ephemeris version {metadata['ephemeris_version']}; Galilean EQJ vectors at Jupiter light departure time; topocentric sightline; oblate one-bar Jupiter; visibility adapter version {metadata['visibility_ephemeris_version']}"),
                      Source("NASA Jupiter dimensions", "https://nssdc.gsfc.nasa.gov/planetary/factsheet/jupiterfact.html",
                             iso(context.now), "71,492 / 66,854 km equatorial/polar radius")]
        if has_grs:
            provenance += [
                Source("GRS longitude reference", self.settings["grs_source_url"], iso(context.now),
                       f"Dated System II extrapolation from {self.settings['grs_reference_utc']}; not a live observation"),
                Source("NASA PDS longitude systems", "https://atmos.nmsu.edu/jsdap/encyclopediawork.html", iso(context.now),
                       "System II: 43.30 degrees at J2000; 870.270 degrees/day at light departure time")]
        scope = peak.astimezone(ZoneInfo(context.observer.timezone)).date().isoformat()
        # A feature can intersect separate observing windows. Keep each local
        # observing night distinct without encoding weather or refined seconds.
        night = (lo.astimezone(ZoneInfo(context.observer.timezone))-timedelta(hours=12)).date().isoformat()
        token = "+".join(sorted(f.key for f in features))+"/"+night
        if combined:
            token += "/"+segment
        title = "Jupiter: "+("simultaneous "+", ".join(labels) if combined else labels[0])
        return Candidate(
            event_id(self.name, context.observer, token), self.name,
            "jupiter.overlap" if combined else "jupiter."+features[0].kind, context.observer.key, scope,
            title, lo, peak, hi, interest, .85 if has_grs else .95, "planetary",
            [("; ".join(labels)+f"; coordinated viewing window {(hi-lo).total_seconds()/60:.0f} minutes; includes the transition itself")
             if combined and transition else
             ("; ".join(labels)+f" overlap for {(hi-lo).total_seconds()/60:.0f} minutes") if combined else labels[0],
             f"Jupiter altitude {position['geometric_altitude_deg']:.1f} degrees; apparent disk {metadata['diameter_arcsec']:.1f} arcsec",
             "Cloud cover, seeing and altitude determine observing quality separately from phenomenon interest",
             CONTACT_NOTE] + (["GRS is a drifting cloud feature; its predicted longitude and timing are approximate"] if has_grs else []),
            provenance, {k:round(v, 2) for k, v in position.items() if k != "diameter_arcsec"}, metadata,
            temporal_kind="opportunity" if combined or features[0].kind == "grs" else "scheduled",
            valid_until=iso(grs_expiry) if has_grs else None,
        )

