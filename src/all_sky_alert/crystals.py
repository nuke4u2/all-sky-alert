"""Explainable ice-optics opportunities: geometry, crystals and obstruction separate."""
from datetime import timedelta
from math import exp
from zoneinfo import ZoneInfo

from .domain import Candidate,DetectionBatch,Source,bounded,event_id,iso,parse

PHENOMENA={'halo22':'22° halo','parhelia':'Sun dogs / parhelia','cza':'Circumzenithal arc',
           'upper_tangent':'Upper tangent arc','lower_tangent':'Lower tangent arc',
           'pillar':'Sun / light pillar','lunar_halo':'Lunar 22° halo','diamond_dust':'Near-surface diamond-dust conditions'}


def possibility(kind,altitude,illumination=1):
    if kind=='cza':return 0<altitude<32.2
    if kind=='lower_tangent':return 22<altitude<29
    if kind=='upper_tangent':return 0<altitude<29
    if kind=='pillar':return -3<altitude<10
    if kind=='lunar_halo':return altitude>5 and illumination>=.5
    return altitude>0 if kind!='diamond_dust' else True


def suitability(kind,conditions,profile,settings):
    m=conditions.metrics
    low=m.get('cloud_low');high=m.get('cloud_high')
    obstruction=low.value if low else None
    confidence=.25
    mode='Coarse cloud-layer opportunity; ice habit/orientation/optical depth unknown'
    ice= min(80,20+high.value*.7) if high and high.value>=10 else 0 if high else None
    micro=[]
    if profile and kind!='diamond_dust':
        for layer in profile['layers']:
            f=layer['fields']
            temperature=f['TMP'];ice_field=f['CIMIXR'];height=f['HGT'];q=f['SPFH'];pressure=f['PRES']
            # Unknown/local-table units must never be interpreted as physical ice.
            if temperature['units']=='K' and ice_field['units'] in ('kg kg**-1','kg/kg') and height['units']=='gpm' and q['units'] in ('kg kg**-1','kg/kg') and pressure['units']=='Pa' and 150<temperature['value']<350 and 0<=q['value']<.1 and 1000<pressure['value']<110000:
                t=temperature['value']-273.15
                vapor=q['value']*pressure['value']/(.622+.378*q['value'])
                humidity=100*vapor/(611.2*exp(17.67*t/(t+243.5)))
                good=temperature['value']<253.15 and height['value']>4000 and humidity>=60 and ice_field['value']>1e-6
                micro.append({'level':layer['level'],'temperature_c':round(temperature['value']-273.15,1),
                    'ice_mixing_ratio':ice_field['value'],'ice_units':ice_field['units'],
                    'humidity_percent':round(humidity,1),'height_m':height['value'],
                    'ice_number':f['NCCICE'],'cloud_water':f['CLMR'],'favorable':good})
        if micro:
            ice=75 if any(row['favorable'] for row in micro) else 15
            confidence=.45
            mode='HRRR sparse cold ice-bearing layer evidence; crystal orientation/habit still unknown'
    if kind=='diamond_dust':
        temp=m.get('temperature_surface');rh=m.get('humidity');wind=m.get('wind_surface')
        if not temp or not rh or not wind:return None
        # RH is referenced to liquid water. Convert saturation vapor pressure to
        # ice below freezing (Magnus approximation), disclose rather than invent fog.
        t=temp.value
        ice_rh=rh.value*exp(17.625*t/(243.04+t)-22.587*t/(273.86+t)) if t<0 else rh.value
        good=t<=settings['diamond_temperature_c'] and ice_rh>=95 and wind.value<=3
        if not good:return None
        ice=65;obstruction=0 # Surface crystals are not a high-cirrus sightline.
        confidence=.25
        mode=f'Surface cold/weak-wind/ice saturation proxy ({ice_rh:.0f}% RH over ice); inversion, fog and crystal observations unknown'
    if ice is None or obstruction is None:return None
    score=round(ice*(1-obstruction/100)/5)*5
    return {'crystal_suitability':ice,'foreground_obstruction_percent':obstruction,'score':score,
            'confidence':confidence,'method':mode,'layers':micro,'orientation':'unknown','habit':'unknown'}


class SkyCrystalDetector:
    name='sky_crystals'
    defaults={'enabled':False,'interval_seconds':1800,'horizon_hours':48,'minimum_opportunity':35,
              'phenomena':list(PHENOMENA),'diamond_temperature_c':-20}

    @staticmethod
    def validate_settings(s):
        bounded(s['interval_seconds'],300,86400,'crystal.interval_seconds')
        bounded(s['horizon_hours'],1,72,'crystal.horizon_hours')
        bounded(s['minimum_opportunity'],0,100,'crystal.minimum_opportunity')
        bounded(s['diamond_temperature_c'],-50,-10,'crystal.diamond_temperature_c')
        if not isinstance(s['phenomena'],list) or not s['phenomena'] or len(s['phenomena'])!=len(set(s['phenomena'])) or set(s['phenomena'])-set(PHENOMENA):raise ValueError('Unknown/duplicate crystal phenomena')

    def __init__(self,settings,conditions,hrrr):
        self.settings,self.conditions,self.hrrr=settings,conditions,hrrr

    # Weather-triggered candidates must always be recomputed after acquisition,
    # unlike stable nightly ephemeris batches. Acquisition changes the cache key,
    # while the prediction's observer/phenomenon/local-day identity stays stable.
    @property
    def cache_identity(self):
        from .storage import fingerprint
        weather=getattr(self.conditions,'_frame',None)
        hrrr=getattr(self.hrrr,'_frame',None)
        return fingerprint({'weather':weather[0] if weather else None,'hrrr':hrrr[0] if hrrr else None})

    def detect(self,context):
        groups={};scopes=set()
        start=context.now.replace(minute=0,second=0,microsecond=0)
        for hour in range(int(self.settings['horizon_hours'])+1):
            when=start+timedelta(hours=hour)
            day=when.astimezone(ZoneInfo(context.observer.timezone)).date().isoformat()
            for kind,label in PHENOMENA.items():
                if kind not in self.settings['phenomena']:continue
                body='Moon' if kind=='lunar_halo' else 'Sun'
                scope=day+'/'+kind
                scopes.add(scope)
                geo=context.astronomy.position(body,when,context.observer)
                illumination=context.astronomy.moon_illumination(when) if body=='Moon' else 1
                altitude=geo.get('geometric_altitude_deg',geo['altitude_deg'])
                if not possibility(kind,altitude,illumination):continue
                snapshot=self.conditions.snapshot_at(when,geo,context.now) if hasattr(self.conditions,'snapshot_at') else self.conditions.at(when,geo)
                try:profile=self.hrrr.at(when,context.now)
                except Exception:profile=None
                assessed=suitability(kind,snapshot,profile,self.settings)
                if not assessed or assessed['score']<self.settings['minimum_opportunity']:continue
                groups.setdefault(scope,[]).append((when,geo,assessed,snapshot,profile))
        events=[]
        for scope,values in groups.items():
            best=max(values,key=lambda row:row[2]['score'])
            when,geo,opportunity,snapshot,profile=best
            kind=scope.split('/')[-1];body='Moon' if kind=='lunar_halo' else 'Sun'
            # Choose contiguous qualifying hours around the best sample; no claim
            # that a gap elsewhere that day belongs to this observing window.
            index=values.index(best);left=right=index
            while left>0 and values[left][0]-values[left-1][0]==timedelta(hours=1):left-=1
            while right+1<len(values) and values[right+1][0]-values[right][0]==timedelta(hours=1):right+=1
            begins=values[left][0];ends=values[right][0]+timedelta(minutes=30)
            # The coarse final half-hour must not extend past a physical altitude
            # limit or useful Moon illumination. Refine only the closing boundary;
            # this is geometric eligibility, not measured halo contact timing.
            def eligible(at):
                position=context.astronomy.position(body,at,context.observer)
                altitude=position.get('geometric_altitude_deg',position['altitude_deg'])
                light=context.astronomy.moon_illumination(at) if body=='Moon' else 1
                return possibility(kind,altitude,light)
            if not eligible(ends):
                lo,hi=values[right][0],ends
                while (hi-lo).total_seconds()>10:
                    middle=lo+(hi-lo)/2
                    if eligible(middle):lo=middle
                    else:hi=middle
                ends=lo
            if ends<=begins:continue
            if ends<=context.now:continue
            sources=[Source('Astronomy Engine','https://github.com/cosinekitty/astronomy',iso(context.now),'Local airless Sun/Moon geometry')]
            if snapshot.source:sources.append(snapshot.source)
            if profile and opportunity['layers']:sources.append(Source('NOAA HRRR',profile['source_url'],profile['retrieved_at'],'Sparse microphysics; issue '+profile['issued_at']))
            expires=snapshot.valid_until
            if profile and opportunity['layers']:
                expires=iso(min(parse(expires),parse(profile['valid_until']))) if expires else profile['valid_until']
            events.append(Candidate(event_id(self.name,context.observer,scope),self.name,'optics.'+kind,context.observer.key,scope,
                PHENOMENA[kind]+' · opportunity',begins,when,ends,60 if kind!='diamond_dust' else 65,
                opportunity['confidence'],'crystals',[opportunity['method'],f"Coarse opportunity {opportunity['score']}/100; not a calibrated probability.",
                    'Never look directly at the Sun; observe sky optics with the Sun shielded, without unfiltered optics.'],sources,geo,
                {'phenomenon':kind,'body':body,'opportunity':opportunity,'model_valid_at':iso(when),
                 'microphysics':{'issued_at':profile['issued_at'],'valid_at':profile['valid_at'],'retrieved_at':profile['retrieved_at']} if profile and opportunity['layers'] else None,
                 'sampling':'Hourly point forecast; no directional cloud path, ice habit or orientation measurement'},
                temporal_kind='opportunity',valid_until=expires))
        return DetectionBatch(events,scopes)
