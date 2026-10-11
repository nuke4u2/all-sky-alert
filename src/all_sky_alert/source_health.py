"""Source-specific validity, independent of transport success and event scoring."""
from dataclasses import asdict, dataclass, field
from datetime import datetime
from math import isfinite

from .domain import iso, parse


@dataclass(frozen=True)
class FreshnessPolicy:
    cadence_seconds: float
    stale_after_seconds: float | None
    invalid_after_seconds: float | None

    def __post_init__(self):
        if not isfinite(self.cadence_seconds) or self.cadence_seconds <= 0:
            raise ValueError("Positive source cadence required")
        if any(value is not None and (not isfinite(value) or value <= 0) for value in (self.stale_after_seconds, self.invalid_after_seconds)):
            raise ValueError("Positive finite freshness thresholds required")
        if self.stale_after_seconds is not None and (self.stale_after_seconds <= 0 or
                self.invalid_after_seconds is None or self.invalid_after_seconds < self.stale_after_seconds):
            raise ValueError("Expected 0 < stale threshold <= invalid threshold")


@dataclass(frozen=True)
class SourceHealth:
    name: str
    kind: str  # observation, forecast, orbit, catalog, local
    checked_at: str
    policy: FreshnessPolicy
    consumers: list[str]
    retrieved_at: str | None = None
    data_at: str | None = None
    issued_at: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    next_refresh: str | None = None
    cached: bool = False
    cache_status: str = "fresh"
    error: str | None = None  # safe exception class/status, never remote text
    available: bool = True
    details: dict = field(default_factory=dict)

    def view(self, now: datetime) -> dict:
        result = asdict(self)
        reference = self.data_at or self.issued_at or (self.retrieved_at if self.details.get("freshness_basis") == "retrieval" else None)
        age = max(0, (now-parse(reference)).total_seconds()) if reference else None
        state = "healthy"
        if self.details.get("disabled"):
            state = "disabled"
        elif not self.available or (self.kind != "local" and self.retrieved_at is None):
            state = "unavailable"
        elif self.valid_until and now > parse(self.valid_until):
            state = "invalid"
        elif reference and parse(reference) > now:
            state = "invalid"
        elif self.policy.invalid_after_seconds is not None and (age is None or age > self.policy.invalid_after_seconds):
            state = "invalid"
        elif self.policy.stale_after_seconds is not None and (age is None or age > self.policy.stale_after_seconds):
            state = "stale"
        elif self.error:
            state = "degraded"
        if state in {"healthy", "degraded"} and self.cache_status == "stale":
            state = "stale"
        if self.kind == "orbit" and age is not None and self.details.get("warning_hours") is not None and \
                age > self.details["warning_hours"]*3600 and state == "healthy":
            state = "warning"
        positions = self.details.get("position_times")
        if positions:
            ages = [(now-parse(stamp)).total_seconds() for stamp in positions]
            fresh = sum(0 <= seconds <= self.details["position_fresh_seconds"] for seconds in ages)
            result["position_fresh_count"] = fresh
            result["oldest_position_age_seconds"] = round(max(ages),1)
            if not fresh and state in {"healthy","degraded"}:
                state = "invalid" if all(seconds < 0 or seconds > self.policy.invalid_after_seconds for seconds in ages) else "stale"
        result.update(status=state, age_seconds=round(age, 1) if age is not None else None,
                      retrieved_age_seconds=round(max(0, (now-parse(self.retrieved_at)).total_seconds()), 1) if self.retrieved_at else None)
        return result

    def record(self, store):
        store.source_health(self.name, asdict(self))

    @classmethod
    def from_dict(cls, data):
        return cls(**{**data, "policy": FreshnessPolicy(**data["policy"])})


def views(store, now):
    return [SourceHealth.from_dict(row).view(now) for row in store.source_health_rows()]
