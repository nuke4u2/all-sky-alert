from dataclasses import asdict, replace
from datetime import timedelta

from all_sky_alert.assessment import planetary
from all_sky_alert.domain import Conditions, DetectionBatch
from all_sky_alert.notifications import SafeDeliveryError, UncertainDeliveryError
from all_sky_alert.policy import lifecycle, priority
from all_sky_alert.service import Service
from all_sky_alert.storage import Store
from conftest import FakeConditions, RecordingAdapter


class FixedDetector:
    name = "planetary"

    def __init__(self, event):
        self.event, self.error, self.empty = event, None, False

    def detect(self, context):
        if self.error:
            raise self.error
        return DetectionBatch([] if self.empty else [self.event], {self.event.scope})


def make_service(config, store, conditions, event, now, adapter=None):
    detector = FixedDetector(event)
    service = Service(config, store=store, conditions=FakeConditions(conditions), detectors=[detector],
                      adapters=[adapter] if adapter else [], clock=lambda: now)
    return service, detector


def test_updates_revisions_dedup_and_restart(config, store, conditions, event, now):
    adapter = RecordingAdapter()
    service, detector = make_service(config, store, conditions, event, now, adapter)
    try:
        service.tick(now)
        service.tick(now + timedelta(minutes=5))
        detail = store.detail(event.id, config.observer.key)
        assert len(detail["revisions"]) == 1
        assert len(detail["alerts"]) == len(adapter.alerts) == 1
        assert detail["deliveries"][0]["status"] == "SENT"
        detector.event = replace(event, title="Updated Jupiter window", geometry={"altitude_deg": 70})
        service.tick(now + timedelta(minutes=10))
        assert len(store.detail(event.id, config.observer.key)["revisions"]) == 2
    finally:
        service.close()
    restarted_store = Store(store.path)
    restarted, _ = make_service(config, restarted_store, conditions, detector.event, now, adapter)
    try:
        restarted.recover()
        restarted.tick(now + timedelta(minutes=15))
        assert len(adapter.alerts) == 1
        restarted.tick(event.start - timedelta(minutes=30))
        assert len(adapter.alerts) == 2
        assert adapter.alerts[-1]["stage"] == "imminent"
    finally:
        restarted.close()


def test_detector_failure_isolation_and_no_withdrawal(config, store, conditions, event, now):
    service, failing = make_service(config, store, conditions, event, now)
    class Healthy:
        name = "other"
        def detect(self, context):
            return DetectionBatch([replace(event, id="other-event", detector="other")], {event.scope})
    try:
        service.tick(now)
        failing.error = ValueError("broken external data")
        service.detectors.append(Healthy())
        service.tick(now + timedelta(minutes=5))
        assert store.detail(event.id, config.observer.key)["state"] != "WITHDRAWN"
        assert store.detail("other-event", config.observer.key) is not None
        assert next(h for h in store.health_rows() if h["name"] == "detector:planetary")["status"] == "error"
        service.tick(event.end + timedelta(minutes=1))
        assert store.detail(event.id, config.observer.key)["state"] == "EXPIRED"
    finally:
        service.close()


def test_withdrawal_and_canceled_outbox(config, store, conditions, event, now):
    adapter = RecordingAdapter()
    adapter.error = SafeDeliveryError("Known failure")
    service, detector = make_service(config, store, conditions, event, now, adapter)
    try:
        service.tick(now)
        detector.empty = True
        service.tick(now + timedelta(minutes=5))
        detail = store.detail(event.id, config.observer.key)
        assert detail["state"] == "WITHDRAWN"
        assert detail["deliveries"][0]["status"] == "CANCELED"
        assert service.upcoming() == []
    finally:
        service.close()


def test_unknown_delivery_no_automatic_retry(config, store, conditions, event, now):
    adapter = RecordingAdapter()
    adapter.error = UncertainDeliveryError("Timeout")
    service, _ = make_service(config, store, conditions, event, now, adapter)
    try:
        service.tick(now)
        adapter.error = None
        service.tick(now + timedelta(minutes=10))
        assert not adapter.alerts
        detail = store.detail(event.id, config.observer.key)
        job = detail["deliveries"][0]
        assert job["status"] == "UNKNOWN" and job["attempts"] == 1
        assert store.retry_delivery(job["id"], now + timedelta(minutes=11))
        service.tick(now + timedelta(minutes=12))
        assert len(adapter.alerts) == 1
    finally:
        service.close()


def test_safe_retry_uses_current_forecast_and_cancels_if_poor(config, store, conditions, event, now):
    adapter = RecordingAdapter()
    adapter.error = SafeDeliveryError("Known rejection")
    service, _ = make_service(config, store, conditions, event, now, adapter)
    try:
        service.tick(now)
        adapter.error = None
        service.conditions.conditions = Conditions({}, "unavailable", None, None)
        service.tick(now + timedelta(minutes=10))
        assert not adapter.alerts
        assert store.detail(event.id, config.observer.key)["deliveries"][0]["status"] == "CANCELED"
    finally:
        service.close()


def test_crash_claim_recovery_and_transaction_uniqueness(config, store, conditions, event, now):
    assessment = planetary(event, conditions)
    payload = {"candidate": event.to_dict(), "assessment": asdict(assessment), "state": "READY", "priority": 70}
    store.save(payload, now, ["ready"], ["recording"])
    store.save(payload, now, ["ready"], ["recording"])
    claimed = store.claim_delivery(now, ["recording"], config.observer.key, ["planetary"])
    assert claimed is not None
    assert store.claim_delivery(now, ["recording"], config.observer.key, ["planetary"]) is None
    store.recover_deliveries()
    detail = store.detail(event.id, config.observer.key)
    assert len(detail["alerts"]) == len(detail["deliveries"]) == 1
    assert detail["deliveries"][0]["status"] == "UNKNOWN"


def test_upcoming_recent_exact_boundaries_and_history_preserved(config, store, conditions, event, now):
    assessment = planetary(event, conditions)
    def save(candidate):
        store.save({"candidate": candidate.to_dict(), "assessment": asdict(assessment), "state": "WATCH", "priority": candidate.interest}, now, [], [])
    save(event)
    for minutes, name in [(0, "boundary-now"), (120, "boundary-two-hours"), (121, "older")]:
        end = now - timedelta(minutes=minutes)
        save(replace(event, id=name, start=end - timedelta(hours=1), peak=end - timedelta(minutes=10), end=end))
    store.advance(now, 60)
    assert [p["candidate"]["id"] for p in store.predictions(config.observer.key, ["planetary"], now, 2)] == [event.id]
    assert {p["candidate"]["id"] for p in store.predictions(config.observer.key, ["planetary"], now, 2, recent=True)} == {"boundary-now", "boundary-two-hours"}
    assert store.predictions("wrong-observer", ["planetary"], now, 2) == []
    assert store.predictions(config.observer.key, [], now, 2) == []
    assert store.revision(event.id, store.detail(event.id, config.observer.key)["revisions"][0]["id"], config.observer.key)["candidate"]["title"] == event.title


def test_past_events_not_recomputed_and_retention(config, store, conditions, event, now):
    service, detector = make_service(config, store, conditions, event, now)
    try:
        service.tick(now)
        detector.event = replace(event, title="Hindsight changed")
        service.tick(event.end + timedelta(minutes=10))
        detail = store.detail(event.id, config.observer.key)
        assert detail["candidate"]["title"] == event.title
        assert detail["state"] == "EXPIRED"
        store.prune(event.end + timedelta(days=100), 90)
        assert store.detail(event.id, config.observer.key) is None
    finally:
        service.close()


def test_no_credentials_needed_and_provider_fault_degrades(config, store, event, now):
    class BrokenConditions:
        def refresh(self, now):
            raise ValueError("failure")
        def at(self, *args):
            raise AssertionError("Do not reuse stale state after refresh failure")
    service = Service(config, store=store, conditions=BrokenConditions(), detectors=[FixedDetector(event)], clock=lambda: now)
    try:
        service.tick(now)
        detail = store.detail(event.id, config.observer.key)
        assert detail["assessment"]["quality"] is None
        assert not detail["deliveries"]
    finally:
        service.close()
