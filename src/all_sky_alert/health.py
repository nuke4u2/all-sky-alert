"""Capability-aware aggregate health, retaining authoritative source age policies."""
from datetime import timedelta

from .domain import iso,parse
from .registry import DETECTORS
from .sources import SOURCES
from .assessment import PROFILES
from .providers import FIELDS


def aggregate(config,names,sources,components,now):
    issues=[]
    initialized=bool(components or sources)
    severity=0
    last=None
    def issue(level,text):
        nonlocal severity
        severity=max(severity,level)
        issues.append(text)
    for row in components:
        name=row['name']
        if name.startswith('worker'):
            if last is None or row['checked_at']>last:last=row['checked_at']
            if row['status']=='error':issue(2,'Prediction worker failed: '+name)
            elif row['status']=='degraded':issue(1,'Partial evaluation errors: '+name)
            cadence=15 if name in ('worker:live','worker:delivery') else config.values['service']['interval_seconds']
            if now-parse(row['checked_at'])>timedelta(seconds=max(120,cadence*3)):issue(2,'Worker heartbeat overdue: '+name)
        elif name=='atmosphere' and row['status']=='error' and config.values['atmosphere']['enabled']:
            issue(1,'Shared atmosphere refresh failed; observing assessment degraded')
    for name in names:
        spec=DETECTORS.get(name)
        if not spec:continue
        label=spec.label or name.replace('_',' ').title()
        for key in spec.dependencies+spec.optional_dependencies:
            prefix=SOURCES[key].health_prefix or key
            rows=[s for s in sources if name in s['consumers'] and s['name'].startswith(prefix)]
            if not config.values['providers'][key]['enabled']:
                if key in spec.dependencies:issue(2,label+': required source disabled ('+key+')')
                continue
            if not rows:
                if initialized:issue(2 if key in spec.dependencies else 1,label+': source initializing/unavailable ('+key+')')
                continue
            for s in rows:
                status=s['status']
                if status=='healthy':continue
                if status=='disabled':
                    issue(2 if key in spec.dependencies else 1,label+': enabled source not active ('+key+')')
                    continue
                critical=key in spec.dependencies and status in ('invalid','unavailable')
                issue(2 if critical else 1,label+': '+s['name']+' '+status)
        if spec.ephemeris:
            rows=[s for s in sources if s['kind']=='local' and name in s['consumers'] and s['name']=='local.ephemeris']
            if initialized and not rows:issue(2,label+': ephemeris not initialized')
            elif any(s['status'] not in ('healthy','disabled') for s in rows):issue(2,label+': local ephemeris unavailable')
    # Shared weather failures reduce observing assessment, not local event geometry.
    weather_metrics={field[0] for field in FIELDS.values()}
    weather_needed=any(weather_metrics.intersection(PROFILES[p].required_metrics) for name in names if name in DETECTORS for p in DETECTORS[name].profiles)
    weather_enabled=config.values['atmosphere']['enabled']
    if initialized and weather_needed and weather_enabled and not any(s['name']=='open-meteo' for s in sources):
        issue(1,'Shared weather initializing/unavailable; observing assessment degraded')
    seeing_needed=any('planetary' in DETECTORS[name].profiles for name in names if name in DETECTORS)
    if initialized and seeing_needed and weather_enabled and config.values['atmosphere']['canadian']['enabled'] and not any(s['name']=='eccc.seeing' for s in sources):
        issue(1,'Canadian seeing initializing/unavailable; wind fallback or unknown seeing')
    for s in sources:
        consumed=(s['name']=='open-meteo' and weather_needed) or (s['name']=='eccc.seeing' and seeing_needed and config.values['atmosphere']['canadian']['enabled'])
        if weather_enabled and consumed and s['status'] not in ('healthy','disabled'):
            issue(1,s['name']+': '+s['status']+'; observing assessment degraded/fallback')
    colors={0:'green',1:'yellow',2:'red'}
    color=colors[severity] if initialized or severity else 'gray'
    return {'status':color,'label':{'green':'Healthy','yellow':'Degraded','red':'Action needed','gray':'Initializing / disabled'}[color],
            'evaluated_at':last,'checked_at':iso(now),'issues':list(dict.fromkeys(issues)),
            'summary':issues[0] if issues else 'Required data are sufficiently fresh.' if initialized else 'Waiting for initial evaluation.'}
