from dataclasses import replace
from datetime import timedelta

import pytest

from all_sky_alert.assessment import planetary, sunset
from all_sky_alert.domain import Assessment, Candidate, Conditions, Metric, event_id, iso
from all_sky_alert.policy import alert_stages, lifecycle


def test_normalized_round_trip_and_identity(event, config):
    assert Candidate.from_dict(event.to_dict()) == event
    assert event_id("x", config.observer, "occurrence") == event_id("x", config.observer, "occurrence")
    assert event_id("x", replace(config.observer, longitude=1), "occurrence") != event_id("x", config.observer, "occurrence")


@pytest.mark.parametrize("changes", [{"interest": 101}, {"occurrence_confidence": -1}, {"interest": float('nan')}])
def test_invalid_candidate_rejected(event, changes):
    with pytest.raises(ValueError):
        replace(event, **changes)


def test_naive_and_reversed_times_rejected(event):
    with pytest.raises(ValueError):
        replace(event, start=event.start.replace(tzinfo=None))
    with pytest.raises(ValueError):
        replace(event, end=event.start - timedelta(seconds=1))


def test_interest_separate_from_weather_and_seeing(event, conditions, now):
    clear = planetary(event, conditions)
    cloudy = planetary(event, replace(conditions, metrics={**conditions.metrics,
        "cloud_total": Metric(100, "%", 0.8, "fixture", "forecast", iso(now))}))
    poor_seeing = planetary(event, replace(conditions, metrics={**conditions.metrics,
        "seeing_arcsec": Metric(3.4, "arcsec", 0.9, "seeing forecast", "calibrated fixture", iso(now))}))
    assert event.interest == 70
    assert cloudy.quality == 0 < clear.quality
    assert poor_seeing.quality < clear.quality
    assert "seeing suitability" in poor_seeing.components
    assert "seeing_wind_proxy" in clear.conditions.metrics
    assert "seeing_arcsec" not in clear.conditions.metrics


def test_profiles_interpret_clouds_differently(event, conditions, now):
    clear_cloud = {**conditions.metrics, **{key: Metric(0, "%", 0.8, "fixture", "fixture", iso(now))
                                          for key in ("cloud_total", "cloud_low", "cloud_mid", "cloud_high")}}
    clear = replace(conditions, metrics=clear_cloud)
    assert planetary(event, clear).quality > planetary(event, conditions).quality
    assert sunset(event, clear).quality < sunset(event, conditions).quality


def test_unknown_is_not_clear(event):
    assessment = planetary(event, Conditions({}, "unavailable", None, None))
    assert assessment.quality is None and assessment.confidence == 0


def test_lifecycle_and_exceptional_weather_bypass(event, conditions, config, now):
    assessment = planetary(event, conditions)
    policy = config.policy(event.kind)
    assert lifecycle(event, assessment, now, policy) == "READY"
    assert lifecycle(event, assessment, event.start - timedelta(minutes=20), policy) == "IMMINENT"
    assert lifecycle(event, assessment, event.start, policy) == "ACTIVE"
    assert lifecycle(event, assessment, event.end, policy) == "EXPIRED"
    bad = Assessment(0, 0.1, {}, ["cloudy"], replace(conditions, status="stale"))
    assert alert_stages(event, bad, now, policy) == []
    eclipse = replace(event, kind="eclipse.total", interest=100)
    assert alert_stages(eclipse, bad, now, policy) == ["ready"]
    assert alert_stages(replace(eclipse, start=now + timedelta(days=5), peak=now + timedelta(days=5),
                                end=now + timedelta(days=5, hours=1)), bad, now, policy) == ["watch"]
    assert alert_stages(event, assessment, event.start, policy) == []
