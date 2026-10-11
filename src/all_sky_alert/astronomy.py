"""Local ephemeris adapter. Future orbit/occultation engines need not replace it."""
from datetime import UTC, datetime
from datetime import timedelta
from functools import lru_cache
from importlib.metadata import version

import astronomy as engine

from .domain import Observer, iso

AU_KM = 149597870.7
# Equatorial diameters; Saturn excludes rings. NASA planetary fact sheets.
DIAMETERS_KM = {"Jupiter": 142984, "Saturn": 120536, "Mars": 6792.4}


class Astronomy:
    version = version("astronomy-engine")

    def position(self, body: str, when: datetime, observer: Observer) -> dict:
        return dict(self._position(body, when, observer))

    @lru_cache(maxsize=8192)
    def _position(self, body: str, when: datetime, observer: Observer) -> dict:
        time = engine.Time(iso(when).replace("+00:00", "Z"))
        site = engine.Observer(observer.latitude, observer.longitude, observer.elevation_m)
        equatorial = engine.Equator(getattr(engine.Body, body), time, site, True, True)
        horizontal = engine.Horizon(time, site, equatorial.ra, equatorial.dec, engine.Refraction.Normal)
        geometric = engine.Horizon(time, site, equatorial.ra, equatorial.dec, engine.Refraction.Airless)
        result = {"altitude_deg": horizontal.altitude, "azimuth_deg": horizontal.azimuth,
                  "geometric_altitude_deg": geometric.altitude}
        if body in DIAMETERS_KM:
            result["diameter_arcsec"] = DIAMETERS_KM[body] / (equatorial.dist * AU_KM) * 206264.806
        return result

    @lru_cache(maxsize=512)
    def moon_illumination(self, when: datetime) -> float:
        return engine.Illumination(engine.Body.Moon, self.time(when)).phase_fraction

    @staticmethod
    def time(when):
        return engine.Time(iso(when).replace("+00:00", "Z"))

    @staticmethod
    def to_datetime(time):
        return time.Utc().replace(tzinfo=UTC)

    @staticmethod
    def site(observer):
        return engine.Observer(observer.latitude, observer.longitude, observer.elevation_m)

    def visible_intervals(self, body, start, end, observer, minimum_altitude=0):
        """Airless center-altitude intervals, refined by the library's root search.

        No mountain/horizon mask or uncertain near-horizon refraction is implied.
        This helper is reusable for eclipse phases and other geometric windows.
        """
        boundaries = {start, end}
        for direction in (engine.Direction.Rise, engine.Direction.Set):
            cursor = start
            while cursor < end:
                crossing = engine.SearchAltitude(getattr(engine.Body, body), self.site(observer), direction,
                    self.time(cursor), (end-cursor).total_seconds()/86400, minimum_altitude)
                if crossing is None:
                    break
                when = self.to_datetime(crossing)
                if start < when < end:
                    boundaries.add(when)
                cursor = max(cursor+timedelta(seconds=1), when+timedelta(seconds=1))
        ordered = sorted(boundaries)
        return [(a,b) for a,b in zip(ordered, ordered[1:])
                if self.position(body, a+(b-a)/2, observer)["geometric_altitude_deg"] > minimum_altitude]

    def solar_eclipses(self, start, end, observer):
        eclipse = engine.SearchLocalSolarEclipse(self.time(start), self.site(observer))
        while self.to_datetime(eclipse.peak.time) < end:
            contacts = []
            for name in ("partial_begin", "total_begin", "peak", "total_end", "partial_end"):
                contact = getattr(eclipse, name)
                if contact is not None:
                    contacts.append({"phase": name, "time": self.to_datetime(contact.time), "altitude_deg": contact.altitude})
            yield {"body": "Sun", "kind": eclipse.kind.name.lower(), "obscuration": eclipse.obscuration,
                   "peak": self.to_datetime(eclipse.peak.time), "contacts": contacts}
            eclipse = engine.NextLocalSolarEclipse(eclipse.peak.time, self.site(observer))

    def lunar_eclipses(self, start, end):
        eclipse = engine.SearchLunarEclipse(self.time(start))
        while self.to_datetime(eclipse.peak) < end:
            peak, contacts = self.to_datetime(eclipse.peak), []
            for phase, minutes in (("penumbral", eclipse.sd_penum), ("partial", eclipse.sd_partial), ("total", eclipse.sd_total)):
                if minutes > 0:
                    contacts += [{"phase": phase+"_begin", "time": peak-timedelta(minutes=minutes)},
                                 {"phase": phase+"_end", "time": peak+timedelta(minutes=minutes)}]
            contacts.append({"phase": "peak", "time": peak})
            yield {"body": "Moon", "kind": eclipse.kind.name.lower(), "obscuration": eclipse.obscuration,
                   "peak": peak, "contacts": sorted(contacts, key=lambda c:c["time"])}
            eclipse = engine.NextLunarEclipse(eclipse.peak)

    def sunset(self, start: datetime, end: datetime, observer: Observer) -> datetime | None:
        site = engine.Observer(observer.latitude, observer.longitude, observer.elevation_m)
        time = engine.SearchRiseSet(engine.Body.Sun, site, engine.Direction.Set, engine.Time(iso(start).replace("+00:00", "Z")),
                                    (end - start).total_seconds() / 86400)
        if time is None:
            return None
        result = time.Utc().replace(tzinfo=UTC)
        return result if start <= result < end else None
