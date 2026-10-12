"""Composition root; independent serial evaluation lanes and delivery outbox."""
from __future__ import annotations

import time
from dataclasses import asdict, replace
from datetime import datetime
from threading import Lock

import httpx

from .assessment import PROFILES
from .astronomy import Astronomy
from .canadian import CanadianSeeing, GeoMetSeeing
from .config import Config
from .domain import Assessment, Candidate, Conditions, DetectionBatch, DetectionContext, Metric, Source, iso, parse, utcnow
from .notifications import GmailAdapter, SafeDeliveryError, UncertainDeliveryError
from .policy import alert_stages, lifecycle, prediction_valid, priority, qualified
from .providers import ConditionsService, FIELDS, OpenMeteo
from .registry import DETECTORS
from .storage import Store, fingerprint
from .sources import build_sources
from .source_health import FreshnessPolicy, SourceHealth, views

class Service:
    def __init__(self, config: Config, *, store=None, conditions=None, detectors=None,
                 profiles=None, astronomy=None, adapters=None, sources=None, camera=None, clock=utcnow):
        self.config, self.clock = config, clock
        from .camera import CameraManager
        self.camera = camera if camera is not None else CameraManager(config, clock=clock)
        self.store = store if store is not None else Store(config.data_dir / "all-sky-alert.sqlite3")
        self.client = httpx.Client(timeout=httpx.Timeout(20, connect=5), follow_redirects=False,
                                   headers={"User-Agent": "AllSkyAlert/0.1"})
        self.conditions = conditions if conditions is not None else ConditionsService(
            self.store, OpenMeteo(self.client), config.observer, config.values["atmosphere"],
            seeing=CanadianSeeing(self.store, GeoMetSeeing(self.client), config.observer,
                config.values["atmosphere"]["canadian"]) if config.values["atmosphere"]["canadian"]["enabled"] else None)
        self.astronomy = astronomy if astronomy is not None else Astronomy()
        self.profiles = profiles if profiles is not None else PROFILES
        names = [d.name for d in detectors] if detectors is not None else [name for name in DETECTORS if config.values["detectors"][name]["enabled"]]
        consumers = {}
        for name in names:
            for dependency in DETECTORS[name].dependencies+DETECTORS[name].optional_dependencies if name in DETECTORS else ():
                consumers.setdefault(dependency, []).append(name)
        self.sources = sources if sources is not None else build_sources(self.client, self.store, config.observer, config.values["providers"], consumers, config.base)
        from .tracks import TrackBuffer
        if 'adsb' in self.sources and hasattr(self.sources['adsb'],'tracks'):
            self.sources['adsb'].tracks=TrackBuffer(config.values['map'])
        self.detectors = detectors if detectors is not None else [DETECTORS[name].factory(config.values["detectors"][name],
            **{key:self.sources[key] for key in DETECTORS[name].dependencies+DETECTORS[name].optional_dependencies},
            **({'conditions':self.conditions} if DETECTORS[name].conditions else {})) for name in names]
        if isinstance(self.conditions, ConditionsService):
            weather_metrics = {field[0] for field in FIELDS.values()}
            self.conditions.consumers = [name for name in names if name not in DETECTORS or any(
                weather_metrics.intersection(getattr(self.profiles.get(profile), "required_metrics", ()))
                for profile in DETECTORS[name].profiles)]
            if self.conditions.seeing:
                self.conditions.seeing.consumers = [name for name in names if name in DETECTORS and any(
                    "seeing_index" in getattr(self.profiles.get(profile), "required_metrics", ()) for profile in DETECTORS[name].profiles)]
                if not self.conditions.seeing.consumers:
                    self.conditions.seeing = None
        self._conditions_available = True
        self._locks = {"slow":Lock(), "live":Lock()}
        self._delivery_lock = Lock()
        self._live_attempts = {}
        if adapters is not None:
            self.adapters = {a.name: a for a in adapters}
        else:
            gmail = config.values["gmail"]
            self.adapters = {"gmail": GmailAdapter(config.path(gmail["token"]), gmail["destination"], self.client)} if gmail["enabled"] else {}

    @property
    def detector_names(self) -> list[str]:
        return [d.name for d in self.detectors]

    def recover(self):
        self.store.recover_deliveries()

    @staticmethod
    def lane(detector):
        return DETECTORS[detector.name].lane if detector.name in DETECTORS else getattr(detector, "lane", "slow")

    def lane_interval(self, lane):
        if lane == "slow":
            return self.config.values["service"]["interval_seconds"]
        return min(self.config.values["detectors"].get(d.name, {}).get("interval_seconds", 300)
                   for d in self.detectors if self.lane(d) == lane)

    def source_views(self, now=None):
        active = set(self.detector_names)
        return [{**s, "consumers":sorted(active.intersection(s["consumers"]))}
                for s in views(self.store, now if now is not None else self.clock()) if active.intersection(s["consumers"])]

    def _batch(self, detector, context, force):
        settings = self.config.values["detectors"].get(detector.name, {})
        key = "detector-batch/"+fingerprint({"version":2, "detector":detector.name,
            "implementation":type(detector).__module__+"."+type(detector).__qualname__, "settings":settings,
            "observer":context.observer.key, "ephemeris":getattr(context.astronomy,"version","injected"), "days":context.days,
            "source_identity":getattr(detector,"cache_identity",None)})
        cached = self.store.cache(key)
        interval = settings.get("interval_seconds", self.config.values["service"]["interval_seconds"])
        if not force and self.lane(detector) != "live" and cached and cached["payload"]:
            try:
                if not 0 <= (context.now-parse(cached["fetched_at"])).total_seconds() < interval:
                    raise ValueError("Detector cache expired")
                payload = cached["payload"]
                if not isinstance(payload["scopes"], list) or len(payload["candidates"]) > 10000:
                    raise ValueError("Invalid cached detector batch")
                return DetectionBatch([Candidate.from_dict(e) for e in payload["candidates"]], set(payload["scopes"]))
            except (KeyError, ValueError, TypeError):
                pass
        batch = detector.detect(context)
        # Cache only valid complete batches; the common contracts still run below.
        if len({e.id for e in batch.candidates}) != len(batch.candidates) or any(e.detector != detector.name or e.observer_key != context.observer.key or e.scope not in batch.scopes for e in batch.candidates):
            raise ValueError("Detector contract violation")
        if self.lane(detector) != "live":
            self.store.cache_success(key, {"candidates":[e.to_dict() for e in batch.candidates], "scopes":sorted(batch.scopes)}, context.now)
        return batch

    def tick(self, now: datetime | None = None, *, lane=None, force=True, deliver=True):
        # Each worker lane is serial; an attempted overlap is skipped, never queued.
        locks = [self._locks[key] for key in ("slow", "live") if lane is None or key == lane]
        if not locks:
            raise ValueError("Unknown evaluation lane")
        acquired = []
        for lock in locks:
            if not lock.acquire(blocking=False):
                for held in acquired:
                    held.release()
                return
            acquired.append(lock)
        try:
            self._tick(now, lane, force, deliver, refresh_clock=now is None)
        finally:
            for lock in acquired:
                lock.release()

    def _tick(self, now, lane, force, deliver, refresh_clock=False):
        now = now if now is not None else self.clock()
        worker = "worker" if lane is None else f"worker:{lane}"
        self.store.health(worker, "evaluating", now, "Evaluating predictions")
        self.store.advance(now, self.config.values["notifications"]["imminent_lead_minutes"])
        if lane in (None, "slow") and (not isinstance(self.conditions, ConditionsService) or self.conditions.consumers):
            try:
                self.conditions.refresh(now)
                self._conditions_available = True
                self.store.health("atmosphere", "ok", now, "Shared atmosphere refresh completed; source freshness is assessed separately")
            except Exception as exc:
                self._conditions_available = False
                self.store.health("atmosphere", "error", now, type(exc).__name__)
        selected = [d for d in self.detectors if lane is None or self.lane(d) == lane]
        if not force:
            selected = [d for d in selected if self.lane(d) != "live" or d.name not in self._live_attempts or
                        not 0 <= (now-self._live_attempts[d.name]).total_seconds() <
                        self.config.values["detectors"].get(d.name, {}).get("interval_seconds", 300)]
        required = {key for d in selected for key in (DETECTORS[d.name].dependencies+DETECTORS[d.name].optional_dependencies if d.name in DETECTORS else getattr(d,"dependencies",()))}
        failed_sources = set()
        for key in sorted(required):
            try:
                self.sources[key].refresh(now)
            except Exception as exc:
                failed_sources.add(key)
                self.store.health(f"source:{key}", "error", now, type(exc).__name__)
                previous = getattr(self.sources.get(key), "health", None)
                if previous:
                    replace(previous, checked_at=iso(now), available=False, error=type(exc).__name__).record(self.store)
        ephemeris_consumers = [d.name for d in self.detectors if
            (DETECTORS[d.name].ephemeris if d.name in DETECTORS else getattr(d, "uses_ephemeris", True))]
        if ephemeris_consumers:
            SourceHealth("local.ephemeris", "local", iso(now), FreshnessPolicy(86400, None, None),
                ephemeris_consumers, details={"library":"Astronomy Engine", "version":getattr(self.astronomy,"version","injected"),
                                            "validity":"Local analytic ephemeris; no observation-age TTL; ideal horizon/limb approximation"}).record(self.store)
        # Live source timestamps can advance during HTTP latency. Background work
        # evaluates after acquisition, not at the request-start instant. Explicit
        # test/CLI times remain deterministic.
        if refresh_clock:
            now = self.clock()
        context = DetectionContext(self.config.observer, now, self.config.values["service"]["prediction_days"], self.astronomy)
        errors = 0
        for detector in selected:
            try:
                if self.lane(detector) == "live":
                    self._live_attempts[detector.name] = now
                dependencies = DETECTORS[detector.name].dependencies if detector.name in DETECTORS else getattr(detector, "dependencies", ())
                if failed_sources.intersection(dependencies):
                    raise RuntimeError("Required provider refresh failed")
                batch = self._batch(detector, context, force)
                ids = set()
                for event in batch.candidates:
                    if event.detector != detector.name or event.observer_key != self.config.observer.key or event.scope not in batch.scopes or event.id in ids:
                        raise ValueError("Detector contract violation")
                    ids.add(event.id)
                # Optional bulk acquisition hook; at() remains an I/O-free snapshot read.
                prepare = getattr(self.conditions, "prepare", None)
                if self._conditions_available and prepare is not None and lane != "live":
                    prepare([event.peak for event in batch.candidates if event.end > now and
                             "seeing_index" in getattr(self.profiles.get(event.profile), "required_metrics", ())])
                for event in batch.candidates:
                    if event.end <= now:  # Freeze past forecasts; do not synthesize hindsight history.
                        continue
                    try:
                        if self._conditions_available:
                            snapshot_at = getattr(self.conditions, "snapshot_at", None)
                            conditions = snapshot_at(event.peak, event.geometry, now) if snapshot_at else self.conditions.at(event.peak, event.geometry)
                        else:
                            conditions = Conditions({}, "unavailable", None, None)
                        assessment = self.profiles[event.profile](event, conditions)
                        policy = self.config.policy(event.kind)
                        payload = {"candidate": event.to_dict(), "assessment": asdict(assessment),
                                   "state": lifecycle(event, assessment, now, policy),
                                   "priority": priority(event, assessment)}
                        self.store.save(payload, now, alert_stages(event, assessment, now, policy), self.delivery_adapters(event))
                    except Exception as exc:
                        errors += 1
                        self.store.health(f"assessment:{event.id}", "error", now, type(exc).__name__)
                self.store.reconcile(detector.name, self.config.observer.key, batch.scopes, ids, now)
                self.store.health(f"detector:{detector.name}", "ok", now, f"{len(batch.candidates)} candidates calculated")
            except Exception as exc:
                errors += 1
                self.store.health(f"detector:{detector.name}", "error", now, type(exc).__name__)
        if deliver and self.config.values["notifications"]["enabled"]:
            self.dispatch(now)
        self.store.prune(now, self.config.values["service"]["retention_days"])
        self.store.health(worker, "degraded" if errors else "ok", now,
                          f"Evaluation complete; {errors} component errors")

    def dispatch(self, now: datetime):
        if not self.config.values["notifications"]["enabled"] or not self._delivery_lock.acquire(blocking=False):
            return
        try:
            self._dispatch(now)
        finally:
            self._delivery_lock.release()

    def _dispatch(self, now):
        self.refresh_milestones(now)
        # Bounded delivery work per cycle; one adapter cannot starve predictions.
        deadline = time.monotonic() + 30
        for _ in range(5):
            if time.monotonic() >= deadline:
                break
            job = self.store.claim_delivery(now, list(self.adapters), self.config.observer.key, self.detector_names)
            if job is None:
                break
            try:
                current = job["current_prediction"]
                event = Candidate.from_dict(current["candidate"])
                raw = current["assessment"]
                cond = raw["conditions"]
                snapshot = Conditions({k: Metric(**v) for k, v in cond["metrics"].items()}, cond["status"],
                    cond["retrieved_at"], Source(**cond["source"]) if cond["source"] else None, cond["notes"],
                    [Source(**s) for s in cond.get("additional_sources", [])], cond.get("valid_until"))
                assessment = Assessment(raw["quality"], raw["confidence"], raw["components"], raw["reasons"], snapshot,
                    raw.get("usable_for_alerts", True), raw.get("valid_until"))
                policy = self.config.policy(event.kind)
                if job['adapter'] not in self.delivery_adapters(event) or not policy["enabled"] or not prediction_valid(event, now) or (job["payload"]["stage"] != "watch" and not qualified(event, assessment, policy, now)):
                    self.store.delivery_result(job["id"], "CANCELED", now, job["attempts"], "Current forecast no longer meets alert policy")
                    continue
                # Preserve original milestone identity but send current forecast, not an obsolete retry snapshot.
                job["payload"]["prediction"] = current
                remote_id = self.adapters[job["adapter"]].send(job["payload"])
                self.store.delivery_result(job["id"], "SENT", now, job["attempts"], remote_id=remote_id)
                self.store.health(f"delivery:{job['adapter']}", "ok", now, "Last send accepted")
            except SafeDeliveryError as exc:
                self.store.delivery_result(job["id"], "FAILED", now, job["attempts"], str(exc))
                self.store.health(f"delivery:{job['adapter']}", "error", now, str(exc))
            except Exception as exc:
                error = str(exc) if isinstance(exc, UncertainDeliveryError) else "Adapter failure; delivery outcome unknown"
                self.store.delivery_result(job["id"], "UNKNOWN", now, job["attempts"], error)
                self.store.health(f"delivery:{job['adapter']}", "unknown", now, error)

    def refresh_milestones(self, now):
        """Cheap clock/policy work; scheduled contacts need no rediscovery or HTTP."""
        for payload in self.store.scheduled(self.config.observer.key,self.detector_names,now):
            event = Candidate.from_dict(payload["candidate"])
            # Live episodes/forecasts require detector evaluation, never a clock-only
            # new 'current' claim based on a future projection or stalled source.
            if event.temporal_kind == "transient":
                continue
            raw = payload["assessment"]
            cond = raw["conditions"]
            snapshot = Conditions({k:Metric(**v) for k,v in cond["metrics"].items()},cond["status"],cond["retrieved_at"],
                Source(**cond["source"]) if cond["source"] else None,cond.get("notes",[]),
                [Source(**s) for s in cond.get("additional_sources",[])],cond.get("valid_until"))
            assessment = Assessment(raw["quality"],raw["confidence"],raw["components"],raw["reasons"],snapshot,
                                    raw.get("usable_for_alerts",True),raw.get("valid_until"))
            policy = self.config.policy(event.kind)
            stages = alert_stages(event,assessment,now,policy)
            state = lifecycle(event,assessment,now,policy)
            if stages or state != payload["state"]:
                self.store.save({**payload,"state":state},now,stages,self.delivery_adapters(event),
                                expected_evaluated_at=payload["evaluated_at"])

    def upcoming(self, now=None):
        return self.store.predictions(self.config.observer.key, self.detector_names,
            now if now is not None else self.clock(), self.config.values["service"]["recent_hours"])

    def delivery_adapters(self,event):
        selected=self.config.values['gmail']['event_types']
        return [name for name in self.adapters if name!='gmail' or
                (self.config.values['gmail']['enabled'] and ('*' in selected or event.detector in selected))]

    def recent(self, now=None):
        return self.store.predictions(self.config.observer.key, self.detector_names,
            now if now is not None else self.clock(), self.config.values["service"]["recent_hours"], recent=True)

    def close(self):
        self.camera.close()
        self.client.close()
