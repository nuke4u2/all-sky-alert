"""Calendar scheduling and summaries of persisted predictions; no detector I/O."""
from datetime import UTC, date, datetime, time, timedelta
from email.message import EmailMessage
from hashlib import sha256
from urllib.parse import quote
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
import time as monotonic_time

from .domain import Assessment, Candidate, Conditions, Metric, Source, iso, parse
from .mail_policy import WEEKDAYS, selected
from .policy import prediction_valid, qualified
from .registry import DETECTORS


SCHEDULED = ('daily_digest', 'weekly_digest')


def wall_time(day, clock, timezone):
    """First repeated instant; nonexistent wall time advances to first valid minute."""
    zone = ZoneInfo(timezone)
    naive = datetime.combine(day, time.fromisoformat(clock))
    for minute in range(181):
        wall = naive + timedelta(minutes=minute)
        matches = []
        for fold in (0, 1):
            instant = wall.replace(tzinfo=zone, fold=fold).astimezone(UTC)
            if instant.astimezone(zone).replace(tzinfo=None) == wall:
                matches.append(instant)
        if matches:
            return min(matches)
    raise ValueError('Local schedule time has no valid instant within three hours')


def slot(config, mode, now):
    settings = config.values['gmail'][mode]
    local = now.astimezone(ZoneInfo(config.observer.timezone))
    day = local.date()
    if mode == 'weekly_digest':
        day -= timedelta(days=(day.weekday()-WEEKDAYS.index(settings['weekday'])) % 7)
    scheduled = wall_time(day, settings['time'], config.observer.timezone)
    if scheduled > now:
        day -= timedelta(days=1 if mode == 'daily_digest' else 7)
        scheduled = wall_time(day, settings['time'], config.observer.timezone)
    expiry = scheduled + timedelta(hours=settings['catch_up_hours'])
    if mode == 'daily_digest':
        expiry = min(expiry, wall_time(day+timedelta(days=1), '00:00', config.observer.timezone))
    return day, scheduled, expiry


def identity(config, mode, day):
    period = day.isoformat() if mode == 'daily_digest' else '%04d-W%02d' % day.isocalendar()[:2]
    key = f'{config.observer.key}/{mode}/{period}'
    return 'digest-'+sha256(key.encode()).hexdigest()[:32], period


def assessment_from(raw):
    cond = raw['conditions']
    snapshot = Conditions({key: Metric(**value) for key, value in cond['metrics'].items()},
        cond['status'], cond.get('retrieved_at'), Source(**cond['source']) if cond.get('source') else None,
        cond.get('notes', []), [Source(**s) for s in cond.get('additional_sources', [])], cond.get('valid_until'))
    return Assessment(raw['quality'], raw['confidence'], raw['components'], raw['reasons'], snapshot,
                      raw.get('usable_for_alerts', True), raw.get('valid_until'))


def summarize(payload, now, policy, high_interest):
    event, assessed = payload['candidate'], payload['assessment']
    conditions = assessed['conditions']
    stale = bool(assessed.get('valid_until') and now > parse(assessed['valid_until']))
    quality = assessed['quality'] if not stale and conditions['status']=='fresh' else None
    poor = (quality is None or quality < policy['minimum_quality'] or stale or not assessed.get('usable_for_alerts', True))
    if event['interest'] >= policy['exceptional_interest'] and poor:
        section = 'Important events — poor or unknown observing conditions'
    elif event['interest'] >= high_interest:
        section = 'High-interest events'
    elif event['profile'] in ('sunset', 'crystals'):
        section = 'Atmospheric / daytime opportunities'
    else:
        section = 'Best observing opportunities'
    metrics = conditions.get('metrics', {})
    seeing=metrics.get('seeing_index')
    if seeing and (stale or (seeing.get('valid_until') and now>parse(seeing['valid_until']))):
        seeing={**seeing,'status':'stale snapshot'}
    return {'id': event['id'], 'title': event['title'], 'kind': event['kind'], 'section': section,
        'start': event['start'], 'peak': event['peak'], 'end': event['end'], 'interest': event['interest'],
        'quality': quality, 'occurrence_confidence': event['occurrence_confidence'],
        'assessment_confidence': assessed['confidence'],
        'atmosphere': 'stale snapshot' if stale else conditions['status'],
        'seeing': seeing, 'cloud': metrics.get('cloud_total'),
        'sources': [*event['sources'], *([conditions['source']] if conditions.get('source') else []),
                    *conditions.get('additional_sources',[])],
        'explanation': event.get('metadata', {}).get('opportunity_band') or next(iter(event.get('reasons', [])), ''),
        'safety': event.get('metadata', {}).get('safety'), 'priority': payload['priority']}


def build(config, store, mode, day, now, *, scheduled_at=None):
    """Same pure database-query builder used for both preview and scheduled mail."""
    settings = config.values['gmail'][mode]
    timezone = config.observer.timezone
    due = scheduled_at or wall_time(day, settings['time'], timezone)
    if mode == 'daily_digest':
        start, end = wall_time(day, '00:00', timezone), wall_time(day+timedelta(days=1), '00:00', timezone)
    else:
        clock=due.astimezone(ZoneInfo(timezone)).strftime('%H:%M')
        start = wall_time(day+timedelta(days=settings['start_days_ahead']), clock, timezone)
        end = wall_time(day+timedelta(days=settings['end_days_ahead']), clock, timezone)
    names = [name for name, spec in DETECTORS.items() if config.values['detectors'][name]['enabled'] and selected(settings, name)
             and (mode == 'daily_digest' or spec.long_range_horizon)]
    notes = []
    if mode == 'weekly_digest':
        for name in names:
            horizon = config.values['detectors'][name][DETECTORS[name].long_range_horizon]
            notes.append(f'{DETECTORS[name].label}: configured geometry horizon {horizon} days; only persisted predictions are included.')
        notes.append('Only registered long-range geometry participates. Weather/seeing may be unknown. Live aircraft, short-term aurora, satellite elements, sunsets and ice optics are not extended into long-range forecasts.')
    notes.append('Summary uses persisted forecasts; no detector or weather request is triggered. Unknown conditions do not mean clear skies.')
    rows = store.prediction_window(config.observer.key, names, start, end, starts_only=mode == 'weekly_digest')
    items = []
    for row in rows:
        event = Candidate.from_dict(row['candidate'])
        policy = config.policy(event.kind)
        if event.end <= now or not policy['enabled'] or not prediction_valid(event, now):
            continue
        if mode == 'daily_digest':
            if not qualified(event, assessment_from(row['assessment']), policy, now):
                continue
        elif (event.interest < max(settings['minimum_interest'], policy['minimum_interest'])
              or event.occurrence_confidence < policy['minimum_confidence']
              or event.start > now+timedelta(days=config.values['detectors'][event.detector][DETECTORS[event.detector].long_range_horizon])):
            continue
        items.append(summarize(row, now, policy, config.values['gmail']['immediate']['minimum_interest']))
    order='interest' if mode=='weekly_digest' else 'priority'
    items.sort(key=lambda row: (-row[order], row['start'], row['id']))
    identifier, period = identity(config, mode, day)
    return {'id': identifier, 'kind': 'digest', 'mode': mode, 'period': period, 'day': day.isoformat(),
        'scheduled_at': iso(due), 'generated_at': iso(now), 'timezone': timezone,
        'window_start': iso(start), 'window_end': iso(end), 'items': items[:settings['max_events']],
        'omitted': max(0, len(items)-settings['max_events']), 'notes': notes,
        'base_url': config.values['gmail']['base_url'].rstrip('/')}


def clean(value):
    # External strings are content, never headers or HTML. Strip embedded controls
    # so a hostile callsign cannot create misleading additional summary sections.
    return ' '.join(str(value).split())[:2000]


def render_digest(payload, recipient):
    message = EmailMessage()
    message['To'] = recipient
    daily = payload['mode'] == 'daily_digest'
    title = 'Daily observing digest' if daily else 'Weekly long-range look-ahead'
    message['Subject'] = f"All Sky Alert · {title} · {payload['day']}"
    message['Message-ID'] = f"<{payload['id']}@all-sky-alert.local>"
    zone = ZoneInfo(payload['timezone'])
    def local(stamp):
        return parse(stamp).astimezone(zone).strftime('%a %b %d, %I:%M %p %Z')
    text = [title, date.fromisoformat(payload['day']).strftime('%A, %B %d, %Y'),
            f"Observer timezone: {payload['timezone']}",
            f"Window: {local(payload['window_start'])} — {local(payload['window_end'])}",
            f"Forecast snapshot: {local(payload['generated_at'])}",
            'Scores/confidence are heuristic ratings, not calibrated probabilities.', '']
    for section in dict.fromkeys(item['section'] for item in payload['items']):
        text.extend([section.upper(), ''])
        for item in (i for i in payload['items'] if i['section'] == section):
            quality = 'Unknown' if item['quality'] is None else f"{item['quality']:g}/100"
            text.extend([clean(item['title']), f"{local(item['start'])} — {local(item['end'])}",
                f"Interest: {item['interest']:g}/100; observing quality: {quality}",
                f"Best viewing / peak: {local(item['peak'])}",
                f"Occurrence confidence: {item['occurrence_confidence']:g}; observing confidence: {item['assessment_confidence']:g}",
                f"Atmosphere: {clean(item['atmosphere'])}"])
            seeing = item['seeing']
            if seeing:
                value = int(seeing['value'])
                labels = ('Cloud masked', 'Very bad', 'Poor', 'Average', 'Good', 'Excellent')
                text.append(f"Seeing: {labels[value] if 0 <= value <= 5 else 'Unknown'} ({value}/5); {clean(seeing.get('status', 'fresh'))}")
            if item['cloud']:
                text.append(f"Cloud: {item['cloud']['value']:g}%")
            text.append(clean(item['explanation']))
            if item['safety']:
                text.extend(['SOLAR VIEWING SAFETY', clean(item['safety']['text']), clean(item['safety']['url'])])
            if payload['base_url']:
                text.append(payload['base_url']+'/events/'+quote(item['id'], safe=''))
            text.append('')
    if not payload['items']:
        text.append('No eligible persisted opportunities in this window. This is not proof that nothing will occur; source/horizon coverage may be limited.')
    if payload['omitted']:
        text.append(f"{payload['omitted']} further opportunities omitted by the configured message limit.")
    if payload['base_url']:
        text.append('All predictions: '+payload['base_url']+'/')
    credits={}
    for item in payload['items']:
        for source in item['sources']:
            try:
                parsed=urlsplit(source['url'])
                if parsed.scheme in ('http','https') and parsed.hostname and not parsed.username:
                    credits[(clean(source['name']),clean(source['url']))]=None
            except (ValueError,TypeError):pass
    if credits:
        text.extend(['','Sources / provenance:'])
        text.extend(name+': '+url for name,url in credits)
    if any(item['cloud'] and item['cloud']['source']=='Open-Meteo' for item in payload['items']):
        text.append('Weather: Open-Meteo, CC BY 4.0 — https://open-meteo.com/')
    if any(item['seeing'] for item in payload['items']):
        text.append('Canadian seeing: Environment and Climate Change Canada; ECCC Data Services End-use Licence — https://eccc-msc.github.io/open-data/licence/readme_en/')
    text.extend(['', *payload['notes']])
    message.set_content('\n'.join(text))
    return message


def dispatch(config, store, adapter, now, *, clock=None, deadline=None):
    """Called under the existing delivery lock. Database uniqueness also guards overlap."""
    if not config.values['notifications']['enabled'] or not config.values['gmail']['enabled'] or adapter is None:
        return
    if deadline is not None and monotonic_time.monotonic()>=deadline:return
    clock=clock or (lambda:now)
    now=clock()
    # Initial/ongoing slow calculation is already doing normal discovery. Wait
    # for its completion rather than freezing an empty partial first-run summary.
    workers = [r for r in store.health_rows() if r['name'] in ('worker', 'worker:slow')]
    latest_worker=max(workers,key=lambda row:row['checked_at']) if workers else None
    ready = not (latest_worker and latest_worker['status']=='evaluating' and
        0 <= (now-parse(latest_worker['checked_at'])).total_seconds() < max(120,config.values['service']['interval_seconds']*3))
    for mode in SCHEDULED:
        if not config.values['gmail'][mode]['enabled']:
            continue
        day, due, expiry = slot(config, mode, now)
        identifier, period = identity(config, mode, day)
        if store.digest(identifier):
            continue
        if now >= expiry:
            store.enqueue_digest({'id': identifier, 'kind': 'digest', 'mode': mode, 'period': period,
                'scheduled_at': iso(due), 'items': []}, config.observer.key, expiry, now, status='EXPIRED')
        elif ready:
            payload = build(config, store, mode, day, now)
            store.enqueue_digest(payload, config.observer.key, expiry, now,
                                 status='PENDING' if payload['items'] else 'EMPTY')
    from .notifications import SafeDeliveryError, UncertainDeliveryError
    for _ in range(4):
        if deadline is not None and monotonic_time.monotonic()>=deadline:break
        now=clock()
        job = store.claim_digest(config.observer.key, now)
        if not job:
            break
        mode, attempts = job['mode'], job['attempts']
        if not config.values['gmail'][mode]['enabled']:
            store.digest_result(job['id'], 'CANCELED', now, attempts, error='Gmail digest mode disabled')
            continue
        try:
            payload = build(config, store, mode, date.fromisoformat(job['payload']['day']), now,scheduled_at=parse(job['scheduled_at']))
            if not payload['items']:
                store.digest_result(job['id'], 'EMPTY', now, attempts, payload=payload)
                continue
            # Persist exact pre-send content. A crash after claim remains UNKNOWN
            # even if it happened before the remote call; never guess acceptance.
            with store.connection() as db:
                from .storage import dumps
                db.execute('UPDATE digest_deliveries SET payload=? WHERE id=?', (dumps(payload), job['id']))
        except Exception:
            store.digest_result(job['id'], 'FAILED', now, attempts, error='Digest preparation failed before send')
            store.health('digest:'+mode, 'error', now, 'Digest preparation failed before send')
            continue
        try:
            remote = adapter.send(payload)
        except SafeDeliveryError as exc:
            store.digest_result(job['id'], 'FAILED', now, attempts, error=str(exc))
            store.health('digest:'+mode, 'error', now, str(exc))
        except Exception as exc:
            error = str(exc) if isinstance(exc, UncertainDeliveryError) else 'Adapter failure; delivery outcome unknown'
            store.digest_result(job['id'], 'UNKNOWN', now, attempts, error=error)
            store.health('digest:'+mode, 'unknown', now, error)
        else:
            store.digest_result(job['id'], 'SENT', clock(), attempts, remote_id=remote)
            store.health('digest:'+mode, 'ok', clock(), 'Last digest accepted by Gmail')


def status(config, store, now):
    result = []
    for mode in SCHEDULED:
        settings = config.values['gmail'][mode]
        day, due, expiry = slot(config, mode, now)
        latest = store.digest_history(config.observer.key, mode)
        sent = store.digest_history(config.observer.key, mode, sent=True)
        identifier, _ = identity(config, mode, day)
        current = store.digest(identifier)
        pending = now < expiry and (current is None or current['status'] in ('PENDING', 'SENDING') or
            (current['status']=='FAILED' and current['attempts']<5))
        if not pending:
            day += timedelta(days=1 if mode == 'daily_digest' else 7)
            due = wall_time(day, settings['time'], config.observer.timezone)
        result.append({'mode': mode, 'label': 'Daily digest' if mode == 'daily_digest' else 'Weekly look-ahead',
            'enabled': config.values['notifications']['enabled'] and config.values['gmail']['enabled'] and settings['enabled'],
            'timezone': config.observer.timezone, 'next_at': iso(due), 'last': latest,
            'last_sent_at': sent['sent_at'] if sent else None})
    return result
