"""Bounded transient observation history, independent of prediction/alert storage."""
from collections import deque
from dataclasses import asdict
from datetime import timedelta
from math import asin, atan2, cos, degrees, radians, sin
from threading import RLock

from .domain import iso, parse
from .aircraft import motion


class TrackBuffer:
    def __init__(self,settings):
        self.settings=settings
        self.lock=RLock()
        self.rows={}

    def update(self,states,now,stale_seconds):
        with self.lock:
            cutoff=now-timedelta(minutes=self.settings['track_minutes'])
            for state in states:
                when=parse(state.position_at)
                if not 0 <= (now-when).total_seconds() <= stale_seconds:continue
                row=self.rows.get(state.icao24)
                if row and when < parse(row['state'].position_at):continue
                if not row:
                    row={'state':state,'points':deque(maxlen=self.settings['max_points'])}
                    self.rows[state.icao24]=row
                if not row['points'] or row['points'][-1]['time'] != state.position_at:
                    row['points'].append({'latitude':state.latitude,'longitude':state.longitude,'time':state.position_at})
                row['state']=state
            for key,row in list(self.rows.items()):
                while row['points'] and parse(row['points'][0]['time']) < cutoff:row['points'].popleft()
                if not row['points']:del self.rows[key]
            order=sorted(self.rows,key=lambda key:self.rows[key]['state'].position_at,reverse=True)
            for key in order[self.settings['max_aircraft']:]:del self.rows[key]

    def snapshot(self,now,stale_seconds):
        with self.lock:
            cutoff=now-timedelta(minutes=self.settings['track_minutes'])
            return [(row['state'],[dict(p) for p in row['points'] if parse(p['time'])>=cutoff])
                    for row in self.rows.values() if 0 <= (now-parse(row['state'].position_at)).total_seconds() <= stale_seconds]


def continuation(state,seconds):
    if state.speed_m_s is None or state.track_deg is None or state.speed_m_s < 1:return []
    lat,lon,bearing=radians(state.latitude),radians(state.longitude),radians(state.track_deg)
    arc=state.speed_m_s*seconds/6371008.8
    endlat=asin(sin(lat)*cos(arc)+cos(lat)*sin(arc)*cos(bearing))
    endlon=lon+atan2(sin(bearing)*sin(arc)*cos(lat),cos(arc)-sin(lat)*sin(endlat))
    return [[state.latitude,state.longitude],[degrees(endlat),(degrees(endlon)+180)%360-180]]


def map_snapshot(service,now):
    config=service.config.values
    result={'checked_at':iso(now),'observer':{'latitude':service.config.observer.latitude,'longitude':service.config.observer.longitude},
            'radius_km':config['map']['radius_km'],'aircraft':[],
            'note':'Solid trails: observed positions. Dashed continuation: <=180s constant-heading estimate, not a filed flight path.'}
    source=service.sources.get('adsb')
    if not source or not hasattr(source,'tracks'):return result
    try:
        source.snapshot(now) # Age/availability check; no I/O.
    except RuntimeError:
        result['note']='Aircraft data unavailable or expired. Coverage is not guaranteed.'
        return result
    enrichment=service.sources.get('enrichment')
    settings=config['detectors']['adsb']
    for state,points in source.tracks.snapshot(now,source.settings['stale_seconds']):
        age=(now-parse(state.position_at)).total_seconds()
        geometry=motion(state,service.config.observer,settings['projection_seconds'],age)
        if geometry['range_km'] > source.settings['radius_nm']*1.852:continue
        try:metadata=enrichment.at(state,now) if enrichment else None
        except Exception:metadata=None
        future=continuation(state,settings['projection_seconds']) if geometry['closest_in_seconds'] is not None else []
        result['aircraft'].append({**asdict(state),'position_age_seconds':round(age,1),
            'valid_until':iso(parse(state.position_at)+timedelta(seconds=source.settings['stale_seconds'])),
            'geometry':geometry,'history':points,'predicted':future,
            'enrichment':{key:metadata.get(key) for key in ('type_name','origin','destination','updated_at','source_name','source_url','age_seconds')} if metadata else None})
    result['aircraft'].sort(key=lambda row:row['geometry']['range_km'])
    return result
