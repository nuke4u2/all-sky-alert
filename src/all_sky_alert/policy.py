"""Lifecycle and notification policy; no delivery I/O."""
from datetime import datetime

from .domain import Assessment, Candidate, parse


def prediction_valid(event: Candidate, now: datetime | None) -> bool:
    return event.valid_until is None or (now is not None and now <= parse(event.valid_until))


def lifecycle(event: Candidate, assessment: Assessment, now: datetime, policy: dict) -> str:
    if event.end <= now:
        return "EXPIRED"
    if event.start <= now:
        return "ACTIVE"
    if (event.start - now).total_seconds() <= policy["imminent_lead_minutes"] * 60:
        return "IMMINENT"
    return "READY" if qualified(event, assessment, policy, now) else "WATCH"


def qualified(event: Candidate, assessment: Assessment, policy: dict, now: datetime | None = None) -> bool:
    if not prediction_valid(event, now):
        return False
    if event.interest >= policy["exceptional_interest"]:
        return event.occurrence_confidence >= policy["minimum_confidence"]
    if now and assessment.valid_until:
        if now > parse(assessment.valid_until):
            return False
    return (event.interest >= policy["minimum_interest"] and assessment.quality is not None
            and assessment.quality >= policy["minimum_quality"]
            and min(event.occurrence_confidence, assessment.confidence) >= policy["minimum_confidence"]
            and assessment.usable_for_alerts)


def alert_stages(event: Candidate, assessment: Assessment, now: datetime, policy: dict) -> list[str]:
    if not policy["enabled"] or event.end <= now or not prediction_valid(event, now):
        return []
    if event.temporal_kind == "transient":
        return (["active" if event.start <= now else "outlook"]
                if qualified(event, assessment, policy, now) else [])
    if event.start <= now:
        return []
    lead = (event.start - now).total_seconds()
    if qualified(event, assessment, policy, now):
        if lead <= policy["imminent_lead_minutes"] * 60:
            return ["imminent"]
        if lead <= policy["ready_lead_hours"] * 3600:
            return ["ready"]
    if event.interest >= policy["exceptional_interest"] and event.occurrence_confidence >= policy["minimum_confidence"] and lead <= policy["watch_lead_hours"] * 3600:
        return ["watch"]
    return []


def priority(event: Candidate, assessment: Assessment) -> float:
    return round(0.8 * event.interest + 0.2 * (assessment.quality or 0), 1)
