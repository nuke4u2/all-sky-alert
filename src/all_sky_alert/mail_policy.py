"""Gmail mode configuration; no delivery, storage, credentials or acquisition."""
from copy import deepcopy
import re
from urllib.parse import urlsplit

from .domain import bounded
from .registry import DETECTORS


MODES = ('immediate', 'daily_digest', 'weekly_digest')
WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
DEFAULTS = {
    'immediate': {'enabled': True, 'minimum_interest': 85, 'event_types': ['*']},
    'daily_digest': {'enabled': False, 'time': '07:00', 'event_types': ['*'],
                     'catch_up_hours': 4, 'max_events': 20},
    'weekly_digest': {'enabled': False, 'weekday': 'Sunday', 'time': '18:00',
                      'minimum_interest': 70, 'start_days_ahead': 7, 'end_days_ahead': 90,
                      'event_types': ['*'], 'catch_up_hours': 24, 'max_events': 20},
    'base_url': '',
}


def migrate(gmail):
    """Normalize legacy selection without modifying input or explicit mode choices."""
    result = deepcopy(gmail)
    if 'event_types' in result:
        legacy = result.pop('event_types')
        validate_types(legacy)
        for mode in MODES:
            if mode not in result:
                result[mode] = {}
            if not isinstance(result[mode], dict):
                raise ValueError('Expected Gmail mode table')
            result[mode].setdefault('event_types', deepcopy(legacy))
    return result


def validate_types(values):
    if (not isinstance(values, list) or any(not isinstance(v, str) or v not in {*DETECTORS, '*'} for v in values)
            or len(values) != len(set(values)) or ('*' in values and len(values) != 1)):
        raise ValueError('Gmail event_types must be registered detector IDs, [] or ["*"]')


def validate(gmail, allowed_hosts):
    for mode in MODES:
        settings = gmail[mode]
        if not isinstance(settings['enabled'], bool):
            raise ValueError('Gmail mode enabled must be boolean')
        validate_types(settings['event_types'])
        if 'minimum_interest' in settings:
            bounded(settings['minimum_interest'], 0, 100, mode+'.minimum_interest')
        if mode == 'immediate':
            continue
        if not isinstance(settings['time'], str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', settings['time']):
            raise ValueError('Gmail schedule time must be HH:MM (24 hour)')
        bounded(settings['catch_up_hours'], .25, 24 if mode == 'daily_digest' else 72, mode+'.catch_up_hours')
        if type(settings['max_events']) is not int or not 1 <= settings['max_events'] <= 100:
            raise ValueError('Digest max_events must be an integer 1–100')
    weekly = gmail['weekly_digest']
    if weekly['weekday'] not in WEEKDAYS:
        raise ValueError('Weekly weekday must be a full English weekday name')
    for key in ('start_days_ahead', 'end_days_ahead'):
        if type(weekly[key]) is not int:
            raise ValueError('Weekly day offsets must be integers')
    if not 7 <= weekly['start_days_ahead'] < weekly['end_days_ahead'] <= 3650:
        raise ValueError('Weekly offsets require 7 <= start < end <= 3650')
    base = gmail['base_url']
    if not isinstance(base, str):
        raise ValueError('gmail.base_url must be a URL or empty')
    if base:
        parsed = urlsplit(base)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or any(ord(c) <= 32 for c in base)
                or parsed.hostname.lower() not in {h.lower() for h in allowed_hosts}):
            raise ValueError('gmail.base_url must use an explicit allowed host and no credentials/query/fragment')
        _ = parsed.port


def selected(settings, detector):
    return '*' in settings['event_types'] or detector in settings['event_types']


def immediate_allowed(gmail, event):
    mode = gmail['immediate']
    return gmail['enabled'] and mode['enabled'] and selected(mode, event.detector) and event.interest >= mode['minimum_interest']
