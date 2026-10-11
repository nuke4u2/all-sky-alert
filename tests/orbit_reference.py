"""Independent verification path: direct SGP4 TEME, UTC GMST and Astronomy Engine.

No product orbital geometry, Skyfield positions, or product solvers are called.
GMST uses UTC as UT1 approximation and zero polar motion; tolerances allow this.
Contact verification is algorithmic on fixed elements, not a recorded sighting.
"""
from datetime import timedelta
from math import asin, atan2, cos, degrees, pi, radians, sin, sqrt

import astronomy as ae
from sgp4.api import Satrec, jday
from sgp4 import omm


def model(elements):
    sat = Satrec()
    omm.initialize(sat,elements)
    return sat


def position(sat,when,latitude,longitude,elevation=0):
    jd,fr = jday(when.year,when.month,when.day,when.hour,when.minute,when.second+when.microsecond/1e6)
    error,r,v = sat.sgp4(jd,fr)
    if error:
        raise ValueError("Independent SGP4 failed")
    centuries = (jd+fr-2451545)/36525
    gmst = radians((280.46061837+360.98564736629*(jd+fr-2451545)+.000387933*centuries**2-centuries**3/38710000)%360)
    x,y,z = cos(gmst)*r[0]+sin(gmst)*r[1],-sin(gmst)*r[0]+cos(gmst)*r[1],r[2]
    lat,lon = radians(latitude),radians(longitude)
    n = 6378.137/sqrt(1-.00669437999014*sin(lat)**2)
    x -= (n+elevation/1000)*cos(lat)*cos(lon)
    y -= (n+elevation/1000)*cos(lat)*sin(lon)
    z -= (n*(1-.00669437999014)+elevation/1000)*sin(lat)
    east = -sin(lon)*x+cos(lon)*y
    north = -sin(lat)*cos(lon)*x-sin(lat)*sin(lon)*y+cos(lat)*z
    up = cos(lat)*cos(lon)*x+cos(lat)*sin(lon)*y+sin(lat)*z
    distance = sqrt(east**2+north**2+up**2)
    return {"altitude_deg":degrees(asin(up/distance)),"azimuth_deg":degrees(atan2(east,north))%360,
            "range_km":distance,"unit":[east/distance,north/distance,up/distance]}


def body(when,latitude,longitude,elevation,name):
    time = ae.Time(when.isoformat().replace("+00:00","Z"))
    site = ae.Observer(latitude,longitude,elevation)
    eq = ae.Equator(getattr(ae.Body,name),time,site,True,True)
    horizontal = ae.Horizon(time,site,eq.ra,eq.dec,ae.Refraction.Airless)
    alt,az = radians(horizontal.altitude),radians(horizontal.azimuth)
    return {"unit":[cos(alt)*sin(az),cos(alt)*cos(az),sin(alt)],
            "altitude_deg":horizontal.altitude,
            "radius_deg":degrees(asin((695700 if name=="Sun" else 1737.4)/(eq.dist*149597870.7)))}


def separation(sat,when,observer,name):
    a = position(sat,when,observer["latitude"],observer["longitude"],observer["elevation_m"])
    b = body(when,observer["latitude"],observer["longitude"],observer["elevation_m"],name)
    x,y = a["unit"],b["unit"]
    cross = (x[1]*y[2]-x[2]*y[1],x[2]*y[0]-x[0]*y[2],x[0]*y[1]-x[1]*y[0])
    return degrees(atan2(sqrt(sum(v*v for v in cross)),sum(u*v for u,v in zip(x,y)))),b["radius_deg"]


def sunlit(sat,when):
    jd,fr=jday(when.year,when.month,when.day,when.hour,when.minute,when.second+when.microsecond/1e6)
    error,r,_=sat.sgp4(jd,fr)
    if error:raise ValueError("Propagation error")
    time=ae.Time(when.isoformat().replace("+00:00","Z"))
    vector=ae.RotateVector(ae.Rotation_EQJ_EQD(time),ae.GeoVector(ae.Body.Sun,time,False))
    sun=[vector.x,vector.y,vector.z]
    norm=sqrt(sum(v*v for v in sun));unit=[v/norm for v in sun]
    along=sum(a*b for a,b in zip(r,unit))
    distance2=sum(a*a for a in r)-along*along
    # Independent distant-Sun cylinder/spherical Earth approximation. Fixed test
    # samples are far from penumbra boundaries; finite-Sun differences are irrelevant.
    return not (along<0 and distance2 < 6378.137**2)


def contacts(sat,center,observer,name):
    def edge(seconds):
        sep,radius = separation(sat,center+timedelta(seconds=seconds),observer,name)
        return sep-radius
    # Independent dense scan/bisection; no shared minimum/root implementation.
    edges=[]
    previous=-5.0
    value=edge(previous)
    for i in range(1,1001):
        current=-5+i*.01
        other=edge(current)
        if (value < 0) != (other < 0):
            lo,hi=previous,current
            for _ in range(15):
                mid=(lo+hi)/2
                if (edge(lo)<0)==(edge(mid)<0):
                    lo=mid
                else:
                    hi=mid
            edges.append(center+timedelta(seconds=(lo+hi)/2))
        previous,value=current,other
    if len(edges)!=2:
        raise ValueError("Expected two independent center-track contacts")
    return edges
