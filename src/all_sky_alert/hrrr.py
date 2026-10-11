"""Optional sparse HRRR microphysics: indexed, bounded GRIB byte ranges only."""
from datetime import timedelta
from threading import Lock
from math import isfinite
import re

import httpx

from .domain import bounded,iso,parse
from .providers import cached_fetch
from .source_health import FreshnessPolicy,SourceHealth
from .storage import fingerprint

FIELDS=('CIMIXR','NCCICE','CLMR','TMP','SPFH','PRES','HGT')
# NCEP published GRIB2 parameter tables; HRRR retains tablesVersion=2 even for
# newer entries, which ecCodes can label "unknown". Verify numeric identity and
# use the documented units, never infer units from the index mnemonic alone.
PARAMETERS={(0,1,82):('CIMIXR','kg kg**-1'),(0,6,29):('NCCICE','kg**-1'),
            (0,1,22):('CLMR','kg kg**-1'),(0,0,0):('TMP','K'),
            (0,1,0):('SPFH','kg kg**-1'),(0,3,0):('PRES','Pa'),(0,3,5):('HGT','gpm')}


def select_ranges(text,levels):
    rows=[]
    for line in text.splitlines():
        parts=line.split(':')
        if len(parts)<6 or not parts[0].isdigit() or not parts[1].isdigit():raise ValueError('Invalid GRIB index')
        rows.append((int(parts[1]),parts[3],parts[4]))
    if len(rows)>4000 or any(b[0]<=a[0] for a,b in zip(rows,rows[1:])):raise ValueError('Invalid GRIB index offsets')
    selected=[]
    for i,(offset,field,level) in enumerate(rows[:-1]):
        match=re.fullmatch(r'(\d+) hybrid level',level)
        if field in FIELDS and match and int(match[1]) in levels:
            selected.append((field,int(match[1]),offset,rows[i+1][0]-1))
    expected={(field,level) for level in levels for field in FIELDS}
    if {(field,level) for field,level,_,_ in selected} != expected or len(selected)!=len(expected):
        raise ValueError('Required HRRR fields/levels missing')
    return selected


def decode_point(raw,observer,run,forecast,level):
    import eccodes as ec
    message=ec.codes_new_from_message(raw)
    try:
        if ec.codes_get(message,'edition')!=2 or int(ec.codes_get(message,'level'))!=level:
            raise ValueError('Unexpected GRIB edition/level')
        issue=str(ec.codes_get(message,'dataDate'))+f"{ec.codes_get(message,'dataTime'):04d}"
        if issue!=run.strftime('%Y%m%d%H%M') or int(ec.codes_get(message,'endStep'))!=forecast:
            raise ValueError('GRIB model issue/forecast mismatch')
        identity=tuple(ec.codes_get(message,key) for key in ('discipline','parameterCategory','parameterNumber'))
        if ec.codes_get(message,'centre')!='kwbc' or identity not in PARAMETERS or ec.codes_get(message,'typeOfLevel')!='hybrid':
            raise ValueError('Unsupported NOAA GRIB parameter/level identity')
        field,units=PARAMETERS[identity]
        from importlib.metadata import version
        point=ec.codes_grib_find_nearest(message,observer.latitude,observer.longitude)[0]
        value=float(point['value'])
        if not isfinite(value) or abs(value)>=1e19 or point['distance']>20:raise ValueError('Invalid/distant GRIB sample')
        return {'value':value,'units':units,'field':field,'latitude':point['lat'],
                'longitude':(point['lon']+180)%360-180,'distance_km':point['distance'],
                'parameter':ec.codes_get(message,'shortName'),'decoder':'ecCodes '+version('eccodes')}
    finally:ec.codes_release(message)


class HRRR:
    name='noaa.hrrr'
    defaults={'enabled':False,'cache_seconds':3600,'max_run_age_hours':8,
              'levels':[20,30],'forecast_hours':[3,6],'max_download_mb':32}
    root='https://noaa-hrrr-bdp-pds.s3.amazonaws.com'

    @staticmethod
    def validate_settings(s):
        if not isinstance(s['enabled'],bool):raise ValueError('hrrr.enabled must be boolean')
        bounded(s['cache_seconds'],3600,21600,'hrrr.cache_seconds')
        bounded(s['max_run_age_hours'],6,12,'hrrr.max_run_age_hours')
        bounded(s['max_download_mb'],4,64,'hrrr.max_download_mb')
        for key,lo,hi,count in (('levels',1,50,3),('forecast_hours',0,18,3)):
            values=s[key]
            if not isinstance(values,list) or not 1<=len(values)<=count or len(set(values))!=len(values) or any(type(v) is not int or not lo<=v<=hi for v in values):
                raise ValueError('Bounded unique HRRR levels/forecast hours required')

    def __init__(self,client,store,observer,settings,consumers,decoder=decode_point):
        self.client,self.store,self.observer,self.settings,self.consumers=client,store,observer,settings,consumers
        self.decoder=decoder
        self._frame=None
        self.health=None
        self._lock=Lock()
        self.key='hrrr-sparse/v1/'+fingerprint({'observer':observer.key,'settings':settings})

    @staticmethod
    def validate(data):
        if not isinstance(data,dict) or not isinstance(data.get('samples'),list) or not 1<=len(data['samples'])<=3:raise ValueError('Invalid sparse profile')
        run=parse(data['issued_at']);parse(data['retrieved_at'])
        units=dict(PARAMETERS.values())
        ranges={'TMP':(150,350),'PRES':(1000,110000),'SPFH':(0,.1),'HGT':(-500,30000),
                'CIMIXR':(0,.1),'CLMR':(0,.1),'NCCICE':(0,1e16)}
        for sample in data['samples']:
            if not 0<=(parse(sample['valid_at'])-run).total_seconds()<=18*3600:raise ValueError('Invalid forecast time')
            if len(sample['layers'])>3 or not sample['layers']:raise ValueError('Invalid layer count')
            for layer in sample['layers']:
                if not 1<=layer['level']<=50 or set(layer['fields'])!=set(FIELDS):raise ValueError('Invalid microphysics fields')
                for field,point in layer['fields'].items():
                    if not isinstance(point,dict) or point.get('field')!=field or point.get('units')!=units[field] or not isfinite(point['value']) or not 0<=point['distance_km']<=20 or not ranges[field][0]<=point['value']<=ranges[field][1]:raise ValueError('Invalid microphysics point')

    def fetch(self,now):
        if not 24<=self.observer.latitude<=50 or not -125<=self.observer.longitude<=-66:
            raise ValueError('HRRR CONUS coverage unsupported here')
        # Synoptic run with conservative publication lag; no multi-run probe burst.
        base=now-timedelta(hours=2)
        run=base.replace(hour=base.hour//6*6,minute=0,second=0,microsecond=0)
        run_key=self.key+'/'+run.strftime('%Y%m%d%H')
        previous=self.store.cache(run_key)
        if previous and previous['payload'] and 0<=(now-parse(previous['payload']['issued_at'])).total_seconds()<=self.settings['max_run_age_hours']*3600:
            self.validate(previous['payload'])
            return previous['payload']
        total=0
        def read(url,maximum,headers=None,expected=200):
            nonlocal total
            with self.client.stream('GET',url,headers=headers,timeout=httpx.Timeout(12,connect=4)) as response:
                response.raise_for_status()
                if response.status_code!=expected:raise ValueError('Server ignored GRIB Range; full-file download refused')
                if expected==206:
                    requested=headers['Range'].removeprefix('bytes=')
                    if not response.headers.get('Content-Range','').startswith('bytes '+requested+'/'):
                        raise ValueError('Mismatched GRIB Content-Range')
                chunks=[];size=0
                for chunk in response.iter_bytes():
                    size+=len(chunk);total+=len(chunk)
                    if size>maximum or total>self.settings['max_download_mb']*1_000_000:raise ValueError('HRRR byte budget exceeded')
                    chunks.append(chunk)
                return b''.join(chunks)
        samples=[]
        for forecast in self.settings['forecast_hours']:
            url=f"{self.root}/hrrr.{run:%Y%m%d}/conus/hrrr.t{run:%H}z.wrfnatf{forecast:02d}.grib2"
            index=read(url+'.idx',500000).decode('ascii')
            layers={level:{'level':level,'fields':{}} for level in self.settings['levels']}
            for field,level,start,end in select_ranges(index,self.settings['levels']):
                size=end-start+1
                if size>8_000_000 or total+size>self.settings['max_download_mb']*1_000_000:raise ValueError('HRRR selected range exceeds budget')
                raw=read(url,size,{'Range':f'bytes={start}-{end}'},206)
                if len(raw)!=size or not raw.startswith(b'GRIB') or not raw.endswith(b'7777'):raise ValueError('Invalid/truncated GRIB record')
                point=self.decoder(raw,self.observer,run,forecast,level)
                if point.get('field')!=field:raise ValueError('GRIB parameter does not match selected index')
                layers[level]['fields'][field]=point
            samples.append({'valid_at':iso(run+timedelta(hours=forecast)),'layers':list(layers.values()),'source_url':url})
        data={'issued_at':iso(run),'retrieved_at':iso(now),'samples':samples,'download_bytes':total,
              'method':'Sparse hybrid-level samples; not a full vertical profile; crystal orientation unknown'}
        self.store.cache_success(run_key,data,now)
        return data

    def refresh(self,now):
        with self._lock:
            policy=FreshnessPolicy(self.settings['cache_seconds'],self.settings['max_run_age_hours']*3600,
                                   self.settings['max_run_age_hours']*3600)
            if not self.settings['enabled']:
                self._frame=None
                self.health=SourceHealth(self.name,'forecast',iso(now),policy,self.consumers,available=False,details={'disabled':True})
            else:
                # Shared failure backoff is at least acquisition cadence, not one
                # retry per forecast time/field. Last good extracted samples persist.
                before=self.store.cache(self.key)
                result=cached_fetch(self.store,self.key,lambda:self.fetch(now),self.validate,now,policy.cadence_seconds,policy.invalid_after_seconds)
                self._frame=(result.data,result.fetched_at)
                cache=self.store.cache(self.key)
                if result.error and cache and cache['failures']>(before['failures'] if before else 0):
                    deadline=max(parse(cache['next_attempt']),now+timedelta(seconds=policy.cadence_seconds))
                    self.store.cache_failure(self.key,cache['failures'],deadline,cache['error']);cache=self.store.cache(self.key)
                self.health=SourceHealth(self.name,'forecast',iso(now),policy,self.consumers,
                    issued_at=result.data['issued_at'] if result.data else None,
                    retrieved_at=result.data['retrieved_at'] if result.data else None,available=result.data is not None,error=result.error,
                    valid_until=iso(min(parse(result.data['issued_at'])+timedelta(hours=self.settings['max_run_age_hours']),
                        max(parse(s['valid_at']) for s in result.data['samples'])+timedelta(hours=1))) if result.data else None,
                    next_refresh=cache['next_attempt'] if cache and cache['next_attempt'] else iso(result.fetched_at+timedelta(seconds=policy.cadence_seconds)) if result.fetched_at else None,
                    details={'method':'Sparse HRRR microphysics; coarse fallback is non-gating','byte_budget_mb':self.settings['max_download_mb']})
            self.health.record(self.store)

    def at(self,when,now):
        if not self._frame or not self._frame[0]:return None
        data,retrieved=self._frame
        age=(now-parse(data['issued_at'])).total_seconds()
        if not 0<=age<=self.settings['max_run_age_hours']*3600:return None
        sample=min(data['samples'],key=lambda s:abs((when-parse(s['valid_at'])).total_seconds()))
        if abs((when-parse(sample['valid_at'])).total_seconds())>3600:return None
        if now>parse(sample['valid_at'])+timedelta(hours=1):return None
        return {**sample,'issued_at':data['issued_at'],'retrieved_at':data['retrieved_at'],
                'valid_until':iso(min(parse(data['issued_at'])+timedelta(hours=self.settings['max_run_age_hours']),parse(sample['valid_at'])+timedelta(hours=1)))}
