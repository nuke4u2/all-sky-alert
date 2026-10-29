from dataclasses import replace
from datetime import timedelta
from threading import Event, Thread

from fastapi.testclient import TestClient

from all_sky_alert.domain import DetectionBatch, iso
from all_sky_alert.providers import ConditionsService
from all_sky_alert.service import Service
from all_sky_alert.source_health import FreshnessPolicy, SourceHealth
from all_sky_alert.storage import Store
from all_sky_alert.web import create_app
from all_sky_alert.assessment import planetary, sunset
from all_sky_alert.domain import Assessment, Conditions, Metric
from all_sky_alert.policy import alert_stages, qualified
from conftest import FakeConditions, FakeProvider, RecordingAdapter


class Detector:
    name = "planetary"
    def __init__(self,event):
        self.event,self.calls = event,0
    def detect(self,context):
        self.calls += 1
        return DetectionBatch([self.event],{self.event.scope})


def test_batch_cadence_restart_and_configuration_invalidation(config, store, conditions, event, now):
    detector = Detector(event)
    service = Service(config,store=store,conditions=FakeConditions(conditions),detectors=[detector],adapters=[])
    try:
        service.tick(now,force=False)
        service.tick(now+timedelta(minutes=5),force=False)
        assert detector.calls==1
        assert len(store.detail(event.id,config.observer.key)["revisions"])==1
        # Assessment, not geometry, updates inside its cached calculation cadence.
        service.conditions.conditions = replace(conditions,metrics={**conditions.metrics,"cloud_total":replace(conditions.metrics["cloud_total"],value=100)})
        service.tick(now+timedelta(minutes=10),force=False)
        assert detector.calls==1
        assert store.detail(event.id,config.observer.key)["assessment"]["quality"]==0
    finally:
        service.close()
    new_detector = Detector(event)
    restarted = Service(config,store=Store(store.path),conditions=FakeConditions(conditions),detectors=[new_detector],adapters=[])
    try:
        restarted.tick(now+timedelta(minutes=15),force=False)
        assert new_detector.calls==0
        config.values["detectors"]["planetary"]["minimum_altitude_deg"] = 30
        restarted.tick(now+timedelta(minutes=20),force=False)
        assert new_detector.calls==1
        restarted.tick(now+timedelta(minutes=80),force=False)
        assert new_detector.calls==2
    finally:
        restarted.close()


def test_corrupt_batch_time_and_duplicate_ids_are_not_trusted(config, store, conditions, event, now):
    detector = Detector(event)
    service = Service(config,store=store,conditions=FakeConditions(conditions),detectors=[detector],adapters=[])
    try:
        service.tick(now,force=False)
        with store.connection() as db:
            db.execute("UPDATE provider_cache SET fetched_at='not-a-time' WHERE key LIKE 'detector-batch/%'")
        service.tick(now+timedelta(minutes=1),force=False)
        assert detector.calls==2
        class Duplicate:
            name = "duplicate"
            def detect(self,context):
                copy = replace(event,detector=self.name)
                return DetectionBatch([copy,copy],{event.scope})
        service.detectors = [Duplicate()]
        service.tick(now,force=False)
        assert next(h for h in store.health_rows() if h["name"]=="detector:duplicate")["status"]=="error"
        with store.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM provider_cache WHERE payload LIKE '%duplicate%'").fetchone()[0]==0
    finally:
        service.close()


def test_shared_provider_single_refresh_failure_isolated(config, store, conditions, event, now):
    class Source:
        calls = 0
        broken = False
        def refresh(self,now):
            self.calls += 1
            if self.broken:
                raise ValueError("untrusted remote text should not be shown")
    class Consumer(Detector):
        dependencies = ("shared",)
        def __init__(self,name):
            self.name=name
            super().__init__(replace(event,id=name,detector=name))
    source = Source()
    a,b = Consumer("a"),Consumer("b")
    independent = Detector(event)
    service = Service(config,store=store,conditions=FakeConditions(conditions),detectors=[a,b,independent],
                      sources={"shared":source},adapters=[])
    try:
        service.tick(now)
        assert source.calls==1 and a.calls==b.calls==independent.calls==1
        source.broken=True
        service.tick(now+timedelta(minutes=5))
        assert source.calls==2 and a.calls==b.calls==1 and independent.calls==2
        assert store.detail("a",config.observer.key)["state"]!="WITHDRAWN"
        health = next(h for h in store.health_rows() if h["name"]=="source:shared")
        assert health["message"]=="ValueError"
    finally:
        service.close()


def test_independent_lanes_do_not_block_or_overlap(config, store, conditions, event, now):
    entered,release,fast_done = Event(),Event(),Event()
    class Slow(Detector):
        def detect(self,context):
            self.calls += 1
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test worker failed to release")
            return DetectionBatch([self.event],{self.event.scope})
    class Fast(Detector):
        name="fast"
        lane="live"
        def detect(self,context):
            fast_done.set()
            return super().detect(context)
    slow = Slow(event)
    fast = Fast(replace(event,id="fast-event",detector="fast"))
    service = Service(config,store=store,conditions=FakeConditions(conditions),detectors=[slow,fast],adapters=[])
    thread = Thread(target=lambda:service.tick(now,lane="slow",deliver=False))
    try:
        thread.start()
        assert entered.wait(3)
        service.tick(now,lane="slow",deliver=False)
        service.tick(now,deliver=False) # All-lane one-shot must also skip an occupied lane.
        service.tick(now,lane="live",deliver=False)
        assert fast_done.is_set() and slow.calls==1
        assert store.detail("fast-event",config.observer.key) is not None
    finally:
        release.set()
        thread.join(5)
        assert not thread.is_alive()
        service.close()


def test_weather_snapshot_age_is_recomputed_without_refresh(config, store, weather, now):
    provider = FakeProvider(weather)
    service = ConditionsService(store,provider,config.observer,config.values["atmosphere"])
    service.refresh(now)
    initial = service.snapshot_at(now+timedelta(hours=1),{},now)
    assert initial.status=="fresh" and initial.valid_until==iso(now+timedelta(hours=1))
    assert service.snapshot_at(now+timedelta(hours=2),{},now+timedelta(minutes=61)).status=="stale"
    assert service.snapshot_at(now+timedelta(hours=7),{},now+timedelta(hours=7)).status=="unavailable"
    assert provider.calls==1


def test_delayed_delivery_expires_without_further_evaluation(config, store, conditions, event, now):
    adapter = RecordingAdapter()
    expiring = replace(conditions,valid_until=iso(now+timedelta(minutes=2)))
    service = Service(config,store=store,conditions=FakeConditions(expiring),detectors=[Detector(event)],adapters=[adapter])
    try:
        service.tick(now,deliver=False)
        assert store.detail(event.id,config.observer.key)["deliveries"][0]["status"]=="PENDING"
        service.dispatch(now+timedelta(minutes=3))
        assert not adapter.alerts
        assert store.detail(event.id,config.observer.key)["deliveries"][0]["status"]=="CANCELED"
    finally:
        service.close()


def test_transient_dedup_across_revisions_and_restart(config, store, conditions, event, now):
    adapter = RecordingAdapter()
    event = replace(event,temporal_kind="transient",start=now+timedelta(minutes=10),peak=now+timedelta(minutes=15),end=now+timedelta(minutes=40))
    detector = Detector(event)
    service = Service(config,store=store,conditions=FakeConditions(conditions),detectors=[detector],adapters=[adapter])
    try:
        service.tick(now)
        assert [a["stage"] for a in adapter.alerts]==["outlook"]
        service.tick(now+timedelta(minutes=11))
        assert [a["stage"] for a in adapter.alerts]==["outlook","active"]
        detector.event = replace(event,title="Revised active forecast",end=now+timedelta(minutes=50))
        service.tick(now+timedelta(minutes=12))
    finally:
        service.close()
    restarted = Service(config,store=Store(store.path),conditions=FakeConditions(conditions),detectors=[detector],adapters=[adapter])
    try:
        restarted.tick(now+timedelta(minutes=13))
        assert len(adapter.alerts)==2
        assert len(store.detail(event.id,config.observer.key)["revisions"])>=3
    finally:
        restarted.close()


def test_source_health_api_ages_even_when_worker_stops(config, store, conditions, event, now):
    clock = [now]
    SourceHealth("live-test","observation",iso(now),FreshnessPolicy(60,120,300),["planetary"],
                 retrieved_at=iso(now),data_at=iso(now)).record(store)
    service = Service(config,store=store,conditions=FakeConditions(conditions),detectors=[Detector(event)],adapters=[],clock=lambda:clock[0])
    try:
        service.tick(now)
        with TestClient(create_app(service,background=False),base_url="http://localhost") as client:
            assert next(s for s in client.get("/api/health").json()["sources"] if s["name"]=="live-test")["status"]=="healthy"
            clock[0]=now+timedelta(minutes=6)
            assert next(s for s in client.get("/api/health").json()["sources"] if s["name"]=="live-test")["status"]=="invalid"
    finally:
        service.close()


def test_additive_schema_one_migration_preserves_predictions(config, store, conditions, event, now):
    service = Service(config,store=store,conditions=FakeConditions(conditions),detectors=[Detector(event)],adapters=[])
    try:
        service.tick(now)
        before = store.detail(event.id,config.observer.key)
        with store.connection() as db:
            db.execute("DROP TABLE source_health")
            db.execute("PRAGMA user_version=1")
        migrated = Store(store.path)
        assert migrated.detail(event.id,config.observer.key)==before
        assert migrated.source_health_rows()==[]
        with migrated.connection() as db:
            assert db.execute("PRAGMA user_version").fetchone()[0]==4
    finally:
        service.close()


def test_metric_expiry_only_affects_consuming_profile(config, conditions, event, now):
    seeing = Metric(5,"index",.8,"ECCC","fixture",iso(event.peak),valid_until=iso(now+timedelta(minutes=5)))
    snapshot = replace(conditions,metrics={**conditions.metrics,"seeing_index":seeing})
    planets = planetary(event,snapshot)
    sunsets = sunset(event,snapshot)
    assert planets.valid_until==seeing.valid_until
    assert sunsets.valid_until is None
    policy = config.policy(event.kind)
    assert qualified(event,planets,policy,now)
    assert not qualified(event,planets,policy,now+timedelta(minutes=10))
    assert qualified(event,sunsets,policy,now+timedelta(minutes=10))


def test_fast_lane_respects_each_detectors_cadence(config, store, conditions, event, now):
    class Live(Detector):
        lane = "live"
        def __init__(self,name):
            self.name = name
            super().__init__(replace(event,id=name,detector=name))
    fast,slower = Live("fast"),Live("forecast")
    config.values["detectors"]["fast"]={"interval_seconds":15}
    config.values["detectors"]["forecast"]={"interval_seconds":300}
    service = Service(config,store=store,conditions=FakeConditions(conditions),detectors=[fast,slower],adapters=[])
    try:
        assert service.lane_interval("live")==15
        service.tick(now,lane="live",force=False)
        service.tick(now+timedelta(seconds=15),lane="live",force=False)
        assert fast.calls==2 and slower.calls==1
        service.tick(now+timedelta(minutes=5),lane="live",force=False)
        assert fast.calls==3 and slower.calls==2
    finally:
        service.close()


def test_background_lifespan_starts_independent_lanes_and_stops(config, store, conditions, event, now):
    entered,release,live_done = Event(),Event(),Event()
    class Slow(Detector):
        def detect(self,context):
            entered.set()
            assert release.wait(5)
            return super().detect(context)
    class Live(Detector):
        name="live-fixture"
        lane="live"
        def detect(self,context):
            batch = super().detect(context)
            live_done.set()
            return batch
    slow = Slow(event)
    live = Live(replace(event,id="live",detector="live-fixture"))
    service = Service(config,store=store,conditions=FakeConditions(conditions),detectors=[slow,live],adapters=[],clock=lambda:now)
    with TestClient(create_app(service,background=True),base_url="http://localhost") as client:
        try:
            assert entered.wait(3) and live_done.wait(3)
            assert client.get("/").status_code==200
            assert client.get("/api/health").status_code==200
        finally:
            release.set()
    assert slow.calls==live.calls==1


def test_unused_canadian_source_is_not_acquired(config, store, weather, conditions, event, now):
    class Seeing:
        consumers = []
        def refresh(self,*args):
            raise AssertionError("Unused seeing must not refresh")
    shared = ConditionsService(store,FakeProvider(weather),config.observer,config.values["atmosphere"],seeing=Seeing())
    detector = Detector(replace(event,detector="sunset",profile="sunset"))
    detector.name = "sunset"
    service = Service(config,store=store,conditions=shared,detectors=[detector],adapters=[])
    try:
        service.tick(now)
        assert service.conditions.seeing is None
        assert store.detail(event.id,config.observer.key) is not None
    finally:
        service.close()


def test_exceptional_weather_bypass_never_bypasses_prediction_validity(config, store, conditions, event, now):
    future = replace(event,interest=100,start=now+timedelta(days=5),peak=now+timedelta(days=5,hours=1),
                     end=now+timedelta(days=5,hours=2),valid_until=iso(now+timedelta(minutes=2)))
    bad = replace(conditions,status="stale",valid_until=iso(now-timedelta(minutes=1)))
    assessment = planetary(future,bad)
    policy = config.policy(future.kind)
    assert qualified(future,assessment,policy,now) # Weather may be bypassed.
    assert alert_stages(future,assessment,now,policy)==["watch"]
    assert not qualified(future,assessment,policy,now+timedelta(minutes=3))
    assert alert_stages(future,assessment,now+timedelta(minutes=3),policy)==[]
    adapter = RecordingAdapter()
    service = Service(config,store=store,conditions=FakeConditions(bad),detectors=[Detector(future)],adapters=[adapter])
    try:
        service.tick(now,deliver=False)
        service.dispatch(now+timedelta(minutes=3))
        assert not adapter.alerts
        assert store.detail(future.id,config.observer.key)["deliveries"][0]["status"]=="CANCELED"
    finally:
        service.close()


def test_non_weather_profile_is_not_forced_through_weather_expiry(config, store, event, now):
    class Observed(Detector):
        name = "observation-fixture"
        uses_ephemeris = False
    event = replace(event,detector=Observed.name,profile="observation",temporal_kind="transient",
                    start=now-timedelta(minutes=1),peak=now+timedelta(minutes=5),end=now+timedelta(minutes=10))
    unused = Conditions({},"unavailable",None,None,valid_until=iso(now-timedelta(hours=1)))
    def observation(candidate,conditions):
        return Assessment(80,.9,{},["Quality from observation geometry; atmosphere is not consumed"],conditions,
                          usable_for_alerts=True)
    service = Service(config,store=store,conditions=FakeConditions(unused),detectors=[Observed(event)],
                      profiles={"observation":observation},adapters=[])
    try:
        service.tick(now)
        prediction = store.detail(event.id,config.observer.key)
        assert prediction["assessment"]["valid_until"] is None
        assert [a["stage"] for a in prediction["alerts"]]==["active"]
        assert store.source_health_rows()==[] # No false ephemeris dependency.
    finally:
        service.close()


def test_current_weather_profiles_own_their_freshness(config, conditions, event, now):
    for profile in (planetary,sunset):
        stale = profile(event,replace(conditions,status="stale"))
        assert not stale.usable_for_alerts
        assert not qualified(event,stale,config.policy(event.kind),now)
