"""Small domain contracts. No transport, persistence or delivery dependencies."""
from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


def utcnow() -> datetime:
    return datetime.now(UTC)


def bounded(value, low: float, high: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")


def iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Timezone-aware datetime required")
    return value.astimezone(UTC).isoformat()


def parse(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone-aware datetime required")
    return result.astimezone(UTC)


@dataclass(frozen=True)
class Observer:
    latitude: float
    longitude: float
    elevation_m: float
    timezone: str

    @property
    def key(self) -> str:
        raw = f"{self.latitude:.6f}/{self.longitude:.6f}/{self.elevation_m:.1f}/{self.timezone}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


def event_id(detector: str, observer: Observer, occurrence: str) -> str:
    return hashlib.sha256(f"v1/{detector}/{observer.key}/{occurrence}".encode()).hexdigest()[:32]


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    retrieved_at: str
    method: str


@dataclass(frozen=True)
class Candidate:
    id: str
    detector: str
    kind: str
    observer_key: str
    scope: str
    title: str
    start: datetime
    peak: datetime
    end: datetime
    interest: float
    occurrence_confidence: float
    profile: str
    reasons: list[str]
    sources: list[Source]
    geometry: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1
    temporal_kind: str = "scheduled"  # scheduled, opportunity, transient
    valid_until: str | None = None  # Scientific input validity; not observing quality.

    def __post_init__(self) -> None:
        if self.temporal_kind not in {"scheduled", "opportunity", "transient"}:
            raise ValueError("Invalid temporal kind")
        if self.valid_until:
            parse(self.valid_until)
        for t in (self.start, self.peak, self.end):
            iso(t)
        if not self.start <= self.peak <= self.end or self.start == self.end:
            raise ValueError("Expected start <= peak <= end and positive duration")
        if not math.isfinite(self.interest) or not 0 <= self.interest <= 100:
            raise ValueError("Interest must be 0..100")
        if not math.isfinite(self.occurrence_confidence) or not 0 <= self.occurrence_confidence <= 1:
            raise ValueError("Occurrence confidence must be 0..1")

    def to_dict(self) -> dict:
        data = asdict(self)
        data.update(start=iso(self.start), peak=iso(self.peak), end=iso(self.end))
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Candidate:
        values = dict(data)
        for key in ("start", "peak", "end"):
            values[key] = parse(values[key])
        values["sources"] = [Source(**s) for s in values["sources"]]
        return cls(**values)


@dataclass(frozen=True)
class Metric:
    value: float
    unit: str
    confidence: float
    source: str
    method: str
    valid_at: str
    status: str = "fresh"
    issued_at: str | None = None
    retrieved_at: str | None = None
    valid_until: str | None = None  # Expiry of this input, only for consuming profiles.

    def __post_init__(self) -> None:
        if not math.isfinite(self.value) or not 0 <= self.confidence <= 1:
            raise ValueError("Invalid metric")
        parse(self.valid_at)
        for timestamp in (self.issued_at, self.retrieved_at, self.valid_until):
            if timestamp is not None:
                parse(timestamp)


@dataclass(frozen=True)
class Conditions:
    metrics: dict[str, Metric]
    status: str  # fresh, stale, unavailable, outside_forecast, disabled
    retrieved_at: str | None
    source: Source | None
    notes: list[str] = field(default_factory=list)
    additional_sources: list[Source] = field(default_factory=list)
    valid_until: str | None = None

    def __post_init__(self):
        if self.valid_until:
            parse(self.valid_until)


@dataclass(frozen=True)
class Assessment:
    quality: float | None
    confidence: float
    components: dict[str, float | None]
    reasons: list[str]
    conditions: Conditions
    # Freshness of the inputs actually used by this profile, not every source.
    usable_for_alerts: bool = True
    valid_until: str | None = None  # live-input validity for delayed delivery

    def __post_init__(self) -> None:
        if self.quality is not None and (not math.isfinite(self.quality) or not 0 <= self.quality <= 100):
            raise ValueError("Quality must be unknown or 0..100")
        if not 0 <= self.confidence <= 1:
            raise ValueError("Assessment confidence must be 0..1")
        if self.valid_until:
            parse(self.valid_until)


@dataclass(frozen=True)
class DetectionContext:
    observer: Observer
    now: datetime
    days: int
    astronomy: Any


@dataclass(frozen=True)
class DetectionBatch:
    candidates: list[Candidate]
    scopes: set[str]  # fully evaluated local dates, including empty ones


class Detector(Protocol):
    name: str
    def detect(self, context: DetectionContext) -> DetectionBatch: ...


class ConditionsSource(Protocol):
    def at(self, when: datetime, geometry: dict) -> Conditions: ...


class AssessmentProfile(Protocol):
    def __call__(self, event: Candidate, conditions: Conditions) -> Assessment: ...


class NotificationAdapter(Protocol):
    name: str
    def send(self, alert: dict) -> str: ...
