"""Observer-local display formatting; persisted timestamps remain aware UTC."""
import re
from zoneinfo import ZoneInfo

from .domain import parse


def local_time(value, timezone, *, precise=False):
    if not value:
        return "Unknown"
    stamp = parse(value).astimezone(ZoneInfo(timezone))
    pattern = "%Y-%m-%d %H:%M:%S.%f %Z" if precise else "%a %b %d %Y, %I:%M %p %Z"
    return stamp.strftime(pattern)


_TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\b")


def local_text(value, timezone):
    """Localize timestamps embedded in persisted provider health messages."""
    def replace(match):
        try:
            return local_time(match.group(), timezone)
        except ValueError:
            return match.group()
    return _TIMESTAMP.sub(replace, value)
