"""Monotonic, completion-paced schedule. Missed captures are never replayed."""
from datetime import UTC
from uuid import uuid4

from .settings import validate_interval


def capture_name(now, kind):
    if now.tzinfo is None or kind not in {"still", "timelapse"}:
        raise ValueError("Aware capture time and known capture kind required")
    return f"{kind}-{now.astimezone(UTC):%Y%m%dT%H%M%S.%fZ}-{uuid4().hex[:12]}.jpg"


class TimelapseSchedule:
    def __init__(self, interval):
        validate_interval(interval)
        self.interval = interval
        self.running = False
        self.deadline = None

    def configure(self, running, interval, now):
        validate_interval(interval)
        if not isinstance(running, bool):
            raise ValueError("Timelapse running must be boolean")
        self.running, self.interval = running, interval
        self.deadline = now + interval if running else None

    def due(self, now):
        return self.running and self.deadline is not None and now >= self.deadline

    def completed(self, now):
        self.deadline = now + self.interval if self.running else None
