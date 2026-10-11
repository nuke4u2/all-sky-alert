"""Observer-local eclipse predictions using the shared ephemeris, not web feeds."""
from datetime import timedelta

from .domain import Candidate, DetectionBatch, Source, bounded, event_id, iso

SOLAR_SAFETY = {
    "text": "Never view the Sun with unfiltered eyes, binoculars or a telescope. Use appropriate solar viewing protection and a securely fitted front-aperture solar filter for optical instruments. Ordinary sunglasses are unsafe. Follow NASA solar-viewing guidance.",
    "url": "https://science.nasa.gov/eclipses/safety/",
}


class EclipseDetector:
    name = "eclipses"
    defaults = {"enabled": False, "horizon_days": 730, "interval_seconds": 21600,
                "solar": True, "lunar": True, "penumbral": False, "minimum_altitude_deg": 0}

    @staticmethod
    def validate_settings(settings):
        bounded(settings["horizon_days"], 1, 3650, "eclipses.horizon_days")
        if not isinstance(settings["horizon_days"], int):
            raise ValueError("eclipses.horizon_days must be integer")
        bounded(settings["interval_seconds"], 300, 604800, "eclipses.interval_seconds")
        bounded(settings["minimum_altitude_deg"], 0, 45, "eclipses.minimum_altitude_deg")
        if not all(isinstance(settings[k], bool) for k in ("solar", "lunar", "penumbral")):
            raise ValueError("Eclipse types must be booleans")

    def __init__(self, settings):
        self.settings = settings

    def detect(self, context):
        start = context.now.replace(hour=0, minute=0, second=0, microsecond=0)-timedelta(days=1)
        end = context.now+timedelta(days=self.settings["horizon_days"])
        scopes = {f"{body}/{(start+timedelta(days=i)).date().isoformat()}"
                  for body in ("Sun", "Moon") for i in range((end-start).days+1)}
        raw = []
        if self.settings["solar"]:
            raw.extend(context.astronomy.solar_eclipses(start, end, context.observer))
        if self.settings["lunar"]:
            raw.extend(context.astronomy.lunar_eclipses(start, end))
        candidates = []
        for eclipse in raw:
            if eclipse["kind"] == "penumbral" and not self.settings["penumbral"]:
                continue
            contacts = eclipse["contacts"]
            begin, finish = min(c["time"] for c in contacts), max(c["time"] for c in contacts)
            intervals = context.astronomy.visible_intervals(eclipse["body"], begin, finish, context.observer,
                                                           self.settings["minimum_altitude_deg"])
            if not intervals or finish <= context.now:
                continue
            maximum = eclipse["peak"]
            peak = min((max(a, min(maximum, b)) for a,b in intervals), key=lambda t:abs((t-maximum).total_seconds()))
            maximum_visible = any(a < maximum < b for a,b in intervals)
            phase_times = {c["phase"]:c["time"] for c in contacts}
            visible_kind = "partial" if eclipse["body"] == "Sun" else "penumbral"
            for phase in ("partial", "total"):
                if phase+"_begin" in phase_times and any(
                    a < phase_times[phase+"_end"] and b > phase_times[phase+"_begin"] for a,b in intervals):
                    visible_kind = "annular" if phase == "total" and eclipse["kind"] == "annular" else phase
            if eclipse["body"] == "Moon" and visible_kind == "penumbral" and not self.settings["penumbral"]:
                continue
            interest = {"total": 100, "annular": 95, "partial": 80, "penumbral": 55}[visible_kind]
            family = "solar" if eclipse["body"] == "Sun" else "lunar"
            scope = f"{eclipse['body']}/{maximum.date().isoformat()}"
            annotated = [{**c, "time":iso(c["time"]),
                "altitude_deg":round(context.astronomy.position(eclipse["body"], c["time"], context.observer)["altitude_deg"], 2),
                "visible":any(a <= c["time"] <= b for a,b in intervals)} for c in contacts]
            metadata = {"version":1, "body":eclipse["body"], "geometric_kind":eclipse["kind"],
                        "visible_kind":visible_kind, "maximum_visible":maximum_visible,
                        "geometric_maximum":iso(maximum), "maximum_obscuration":round(eclipse["obscuration"], 5),
                        "contacts":annotated, "visible_intervals":[{"start":iso(a), "end":iso(b)} for a,b in intervals],
                        "ephemeris_version":getattr(context.astronomy, "version", "injected"),
                        "minimum_altitude_deg":self.settings["minimum_altitude_deg"]}
            if family == "solar":
                metadata["safety"] = SOLAR_SAFETY
            reasons = [f"{visible_kind.title()} {family} eclipse phases occur above the configured horizon",
                       f"Geometric maximum obscuration {eclipse['obscuration']*100:.1f}%; maximum {'visible' if maximum_visible else 'below observing horizon; only other phases visible'}",
                       "Interest is independent of the weather forecast; inspect local phase visibility and contacts",
                       "Ideal airless center-altitude horizon; terrain, lunar limb topography and refraction uncertainty are not modeled"]
            candidates.append(Candidate(event_id(self.name, context.observer, scope), self.name,
                f"eclipse.{family}.{visible_kind}", context.observer.key, scope,
                f"{visible_kind.title()} {family} eclipse", intervals[0][0], peak, intervals[-1][1],
                interest, .98, "eclipse", reasons,
                [Source("Astronomy Engine", "https://github.com/cosinekitty/astronomy", iso(context.now),
                        f"Local eclipse/phase/horizon calculations, version {metadata['ephemeris_version']}; no network ephemeris")],
                {k:round(v, 2) for k,v in context.astronomy.position(eclipse["body"], peak, context.observer).items()}, metadata))
        return DetectionBatch(candidates, scopes)
