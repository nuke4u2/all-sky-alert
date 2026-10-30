"""Local Galilean geometry; no weather, transport or notification dependencies."""
from dataclasses import dataclass
from math import atan2, degrees, sqrt

import astronomy as engine

from .astronomy import AU_KM, Astronomy

EQUATOR_KM, POLAR_KM, SUN_KM = 71492.0, 66854.0, 695700.0
MOONS = ("Io", "Europa", "Ganymede", "Callisto")
PERIOD_DAYS = dict(zip(MOONS, (1.769137786, 3.551181, 7.154553, 16.689018)))


def dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def unit(v):
    length = sqrt(dot(v, v))
    return tuple(x/length for x in v)


def xyz(vector):
    return vector.x, vector.y, vector.z


def disk_margin(vector, view, pole, shrink=0):
    """Signed moon-center distance inside Jupiter's projected oblate limb.

    Shadow cone cross sections use a first-order finite-Sun contraction.
    This is not a finite moon silhouette or a photometric disappearance model.
    """
    east = unit(cross(pole, view))
    north = cross(view, east)
    tilt = dot(pole, view)
    a = EQUATOR_KM-shrink
    b = sqrt(EQUATOR_KM**2*tilt**2+POLAR_KM**2*(1-tilt**2))-shrink
    if min(a, b) <= 0:
        return -1.0
    return 1-sqrt((dot(vector, east)/a)**2+(dot(vector, north)/b)**2)


@dataclass(frozen=True)
class JovianState:
    margins: dict
    central_meridian_ii: float


class JovianEphemeris:
    """Astronomy Engine EQJ vectors, topocentric sightline and Jovian light time."""
    version = "center-geometry-v1"
    library_version = Astronomy.version

    def at(self, when, observer):
        reception = Astronomy.time(when)
        jupiter = engine.BackdatePosition(reception, engine.Body.Earth, engine.Body.Jupiter, False)
        site = engine.ObserverVector(reception, Astronomy.site(observer), False)
        # The geocentric light-time solution is sufficient at this model's minute
        # planning scale; topocentric parallax is retained in the sightline.
        view = unit(tuple(s-j for s, j in zip(xyz(site), xyz(jupiter))))
        departure = jupiter.t
        pole = unit(xyz(engine.RotationAxis(engine.Body.Jupiter, departure).north))
        helio = xyz(engine.HelioVector(engine.Body.Jupiter, departure))
        solar_view = unit(tuple(-x for x in helio))
        solar_distance = sqrt(dot(helio, helio))*AU_KM
        moons = engine.JupiterMoons(departure)
        margins = {}
        for moon in MOONS:
            r = tuple(x*AU_KM for x in xyz(getattr(moons, moon.lower())))
            depth = dot(r, view)
            limb = disk_margin(r, view, pole)
            margins[moon, "transit"] = limb if depth > 0 else -1.0
            margins[moon, "occultation"] = limb if depth < 0 else -1.0
            solar_depth = dot(r, solar_view)
            # Jupiter's umbra narrows with distance behind the planet.
            shrink = max(0, -solar_depth)*(SUN_KM-EQUATOR_KM)/solar_distance
            margins[moon, "eclipse"] = disk_margin(r, solar_view, pole, shrink) if solar_depth < 0 else -1.0
            # A moon in front of Jupiter as seen from the Sun casts a center ray
            # onto the ellipsoid. Only an Earth-facing illuminated surface counts.
            margins[moon, "shadow_transit"] = self.shadow_margin(r, solar_view, view, pole) if solar_depth > 0 else -1.0

        # Ascending node of Jupiter's equator on the J2000 Earth equator.
        node = unit((-pole[1], pole[0], 0))
        equator_y = cross(pole, node)
        angle = degrees(atan2(dot(view, equator_y), dot(view, node)))
        cm = (43.30+870.270*departure.tt-angle) % 360
        return JovianState(margins, cm)

    @staticmethod
    def shadow_margin(r, sun, view, pole):
        """Sun-center ray/oblate ellipsoid intersection, Earth-facing only."""
        def metric(a, b):
            return dot(a, b)/EQUATOR_KM**2 + dot(a, pole)*dot(b, pole)*(1/POLAR_KM**2-1/EQUATOR_KM**2)
        # Ray travels from moon away from Sun: r - distance*sun.
        aa, bb, cc = metric(sun, sun), -2*metric(r, sun), metric(r, r)-1
        discriminant = bb*bb-4*aa*cc
        # Continuous signed limb margin allows root refinement.
        if discriminant <= 0:
            return -sqrt(abs(discriminant)/(4*aa))
        distance = (-bb-sqrt(discriminant))/(2*aa)
        if distance <= 0:
            return -1.0
        surface = tuple(x-distance*s for x, s in zip(r, sun))
        normal = tuple(x/EQUATOR_KM**2+dot(surface, pole)*p*(1/POLAR_KM**2-1/EQUATOR_KM**2)
                       for x, p in zip(surface, pole))
        return min(sqrt(discriminant/(4*aa)), dot(unit(normal), view))


def boundary(function, lo, hi):
    """One-second numerical bracket, not a physical accuracy guarantee."""
    inside = function(lo) > 0
    while (hi-lo).total_seconds() > 1:
        middle = lo+(hi-lo)/2
        if (function(middle) > 0) == inside:
            lo = middle
        else:
            hi = middle
    return lo+(hi-lo)/2


def intervals(times, values, function):
    """Positive intervals with refined crossings; incomplete edge runs omitted."""
    result, start = [], None
    for i in range(1, len(times)):
        before, after = values[i-1] > 0, values[i] > 0
        if before == after:
            continue
        contact = boundary(function, times[i-1], times[i])
        if after:
            start = contact
        elif start is not None:
            result.append((start, contact))
            start = None
    return result

