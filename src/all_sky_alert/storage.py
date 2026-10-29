"""SQLite repository and transactional notification outbox."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from .domain import iso, parse


def dumps(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def substantive(value):
    if isinstance(value, dict):
        return {k: substantive(v) for k, v in value.items() if k not in {"retrieved_at", "evaluated_at", "created_at"}}
    if isinstance(value, list):
        return [substantive(v) for v in value]
    return value


def fingerprint(data: dict) -> str:
    return hashlib.sha256(dumps(substantive(data)).encode()).hexdigest()


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, 4):
                raise RuntimeError("Unsupported database version; restore or migrate before starting")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS predictions (
              id TEXT PRIMARY KEY, observer TEXT NOT NULL, detector TEXT NOT NULL,
              scope TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
              state TEXT NOT NULL, priority REAL NOT NULL, payload TEXT NOT NULL,
              fingerprint TEXT NOT NULL, created_at TEXT NOT NULL, evaluated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS predictions_time ON predictions(observer, end, start);
            CREATE TABLE IF NOT EXISTS revisions (
              id INTEGER PRIMARY KEY, event_id TEXT NOT NULL REFERENCES predictions(id) ON DELETE CASCADE,
              evaluated_at TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alerts (
              id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES predictions(id) ON DELETE CASCADE,
              stage TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL,
              UNIQUE(event_id, stage)
            );
            CREATE TABLE IF NOT EXISTS deliveries (
              id INTEGER PRIMARY KEY, alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
              adapter TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              next_attempt TEXT NOT NULL, error TEXT, remote_id TEXT,
              UNIQUE(alert_id, adapter)
            );
            CREATE TABLE IF NOT EXISTS provider_cache (
              key TEXT PRIMARY KEY, fetched_at TEXT, payload TEXT, failures INTEGER NOT NULL DEFAULT 0,
              next_attempt TEXT, error TEXT
            );
            CREATE TABLE IF NOT EXISTS health (
              name TEXT PRIMARY KEY, status TEXT NOT NULL, checked_at TEXT NOT NULL, message TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_health (
              name TEXT PRIMARY KEY, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS track_episodes (
              observer TEXT NOT NULL, aircraft TEXT NOT NULL, episode_id TEXT NOT NULL,
              started_at TEXT NOT NULL, last_position_at TEXT NOT NULL,
              payload TEXT NOT NULL, PRIMARY KEY(observer, aircraft)
            );
            CREATE TABLE IF NOT EXISTS digest_deliveries (
              id TEXT PRIMARY KEY, observer TEXT NOT NULL, mode TEXT NOT NULL,
              period TEXT NOT NULL, scheduled_at TEXT NOT NULL, expires_at TEXT NOT NULL,
              created_at TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              next_attempt TEXT NOT NULL, payload TEXT NOT NULL, error TEXT, remote_id TEXT, sent_at TEXT,
              UNIQUE(observer, mode, period)
            );
            CREATE INDEX IF NOT EXISTS digest_due ON digest_deliveries(observer,status,next_attempt);
            PRAGMA user_version=4;
            """)

    @contextmanager
    def connection(self):
        readonly=getattr(self,'_read_only',False)
        db = sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro' if readonly else self.path, timeout=10, uri=readonly)
        db.row_factory = sqlite3.Row
        if readonly:db.execute('PRAGMA query_only=ON')
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            with db:
                yield db
        finally:
            db.close()

    @classmethod
    def open_readonly(cls,path):
        """Preview-only access: no initialization, schema migration or credentials."""
        path=Path(path)
        if not path.is_file():raise FileNotFoundError('No persisted prediction database; run the normal service first')
        result=cls.__new__(cls);result.path=path;result._read_only=True
        return result

    def health(self, name: str, status: str, now: datetime, message: str) -> None:
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO health VALUES (?,?,?,?)", (name, status, iso(now), message))

    def health_rows(self) -> list[dict]:
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM health ORDER BY name")]

    def source_health(self, name: str, payload: dict):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO source_health VALUES (?,?)", (name, dumps(payload)))

    def source_health_rows(self):
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM source_health ORDER BY name")]

    def cache(self, key: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM provider_cache WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        if result["payload"]:
            result["payload"] = json.loads(result["payload"])
        return result

    def cache_success(self, key: str, payload: dict, now: datetime) -> None:
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO provider_cache VALUES (?,?,?,0,NULL,NULL)",
                       (key, iso(now), dumps(payload)))

    def cache_failure(self, key: str, failures: int, retry_at: datetime, error: str) -> None:
        with self.connection() as db:
            db.execute("""INSERT INTO provider_cache(key,failures,next_attempt,error) VALUES (?,?,?,?)
              ON CONFLICT(key) DO UPDATE SET failures=excluded.failures,
                next_attempt=excluded.next_attempt,error=excluded.error""", (key, failures, iso(retry_at), error))

    def delete_cache(self, key):
        with self.connection() as db:
            db.execute("DELETE FROM provider_cache WHERE key=?", (key,))

    def track_episode(self, observer, aircraft, position_at, timeout_seconds, payload):
        """Continuity persists independently of the prediction's much shorter validity.

        Do not extend last_position_at from an HTTP retrieval or an old state. A gap
        beyond the hysteresis timeout begins a new identity, even after restart.
        """
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM track_episodes WHERE observer=? AND aircraft=?", (observer,aircraft)).fetchone()
            if row and position_at <= parse(row["last_position_at"]):
                return dict(row)
            continuity = row and 0 <= (position_at-parse(row["last_position_at"])).total_seconds() <= timeout_seconds
            start = row["started_at"] if continuity else iso(position_at)
            episode = row["episode_id"] if continuity else hashlib.sha256(f"{observer}/{aircraft}/{start}".encode()).hexdigest()[:32]
            db.execute("INSERT OR REPLACE INTO track_episodes VALUES (?,?,?,?,?,?)",
                       (observer,aircraft,episode,start,iso(position_at),dumps(payload)))
            return {"episode_id":episode,"started_at":start,"last_position_at":iso(position_at),"payload":payload}

    def scheduled(self, observer, detectors, now):
        # Candidate time policy is reevaluated from existing objects, no geometry/HTTP.
        return self.predictions(observer,detectors,now,2)

    def save(self, payload: dict, now: datetime, stages: list[str], adapters: list[str], *, expected_evaluated_at=None) -> bool:
        event = payload["candidate"]
        stamp, signature = iso(now), fingerprint(payload)
        with self.connection() as db:
            row = db.execute("SELECT * FROM predictions WHERE id=?", (event["id"],)).fetchone()
            if expected_evaluated_at is not None and (row is None or row["evaluated_at"] != expected_evaluated_at):
                return False  # A detector update won the race; retry policy on its newer object.
            created = row["created_at"] if row else stamp
            payload = {**payload, "created_at": created, "evaluated_at": stamp}
            changed = row is None or row["fingerprint"] != signature
            db.execute("""INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET start=excluded.start,end=excluded.end,state=excluded.state,
                priority=excluded.priority,payload=excluded.payload,fingerprint=excluded.fingerprint,
                evaluated_at=excluded.evaluated_at""",
                       (event["id"], event["observer_key"], event["detector"], event["scope"],
                        event["start"], event["end"], payload["state"], payload["priority"],
                        dumps(payload), signature, created, stamp))
            if changed:
                db.execute("INSERT INTO revisions(event_id,evaluated_at,payload) VALUES (?,?,?)",
                           (event["id"], stamp, dumps(payload)))
            for stage in stages:
                alert_id = f"{event['id']}:{stage}"
                alert = {"id": alert_id, "event_id": event["id"], "stage": stage,
                         "created_at": stamp, "prediction": payload}
                added = db.execute("INSERT OR IGNORE INTO alerts VALUES (?,?,?,?,?)",
                                   (alert_id, event["id"], stage, stamp, dumps(alert))).rowcount
                if added:
                    for adapter in adapters:
                        db.execute("INSERT INTO deliveries(alert_id,adapter,status,next_attempt) VALUES (?,?,?,?)",
                                   (alert_id, adapter, "PENDING", stamp))
        return changed

    def _state(self, db, row, state: str, now: datetime):
        payload = json.loads(row["payload"])
        payload["state"] = state
        payload["evaluated_at"] = iso(now)
        db.execute("UPDATE predictions SET state=?,payload=?,fingerprint=?,evaluated_at=? WHERE id=?",
                   (state, dumps(payload), fingerprint(payload), iso(now), row["id"]))
        db.execute("INSERT INTO revisions(event_id,evaluated_at,payload) VALUES (?,?,?)",
                   (row["id"], iso(now), dumps(payload)))
        if state in {"WITHDRAWN", "EXPIRED"}:
            db.execute("""UPDATE deliveries SET status='CANCELED',error='Event no longer upcoming'
                WHERE status IN ('PENDING','FAILED') AND alert_id IN
                (SELECT id FROM alerts WHERE event_id=?)""", (row["id"],))

    def advance(self, now: datetime, imminent_minutes: float) -> None:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM predictions WHERE state NOT IN ('EXPIRED','WITHDRAWN')").fetchall()
            for row in rows:
                if parse(row["end"]) <= now:
                    state = "EXPIRED"
                elif parse(row["start"]) <= now:
                    state = "ACTIVE"
                elif parse(row["start"]) <= now + timedelta(minutes=imminent_minutes):
                    state = "IMMINENT"
                else:
                    continue
                if row["state"] != state:
                    self._state(db, row, state, now)

    def reconcile(self, detector: str, observer: str, scopes: set[str], seen: set[str], now: datetime):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM predictions WHERE detector=? AND observer=? AND end>? AND state!='WITHDRAWN'",
                              (detector, observer, iso(now))).fetchall()
            for row in rows:
                if row["scope"] in scopes and row["id"] not in seen:
                    self._state(db, row, "WITHDRAWN", now)

    def predictions(self, observer: str, detectors: list[str], now: datetime, recent_hours: float,
                    recent: bool = False) -> list[dict]:
        if not detectors:
            return []
        placeholders = ",".join("?" for _ in detectors)
        where = "end<=? AND end>=?" if recent else "end>? AND state!='WITHDRAWN'"
        args = [observer, *detectors, iso(now)]
        if recent:
            args.append(iso(now - timedelta(hours=recent_hours)))
        order = "end DESC" if recent else "priority DESC, start ASC"
        with self.connection() as db:
            rows = db.execute(f"SELECT payload FROM predictions WHERE observer=? AND detector IN ({placeholders}) AND {where} ORDER BY {order}", args).fetchall()
            return [json.loads(r["payload"]) for r in rows]

    def detail(self, id: str, observer: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT payload FROM predictions WHERE id=? AND observer=?", (id, observer)).fetchone()
            if not row:
                return None
            payload = json.loads(row["payload"])
            payload["revisions"] = [dict(r) for r in db.execute("SELECT id,evaluated_at FROM revisions WHERE event_id=? ORDER BY id DESC LIMIT 50", (id,))]
            payload["alerts"] = [dict(r) for r in db.execute("SELECT id,stage,created_at FROM alerts WHERE event_id=? ORDER BY created_at", (id,))]
            payload["deliveries"] = [dict(r) for r in db.execute("SELECT deliveries.* FROM deliveries JOIN alerts ON alert_id=alerts.id WHERE event_id=?", (id,))]
            return payload

    def revision(self, event_id: str, revision_id: int, observer: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT revisions.payload FROM revisions JOIN predictions ON event_id=predictions.id WHERE event_id=? AND revisions.id=? AND observer=?",
                             (event_id, revision_id, observer)).fetchone()
            return json.loads(row[0]) if row else None

    def inbox(self, observer: str, limit: int = 20) -> list[dict]:
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT alerts.payload FROM alerts JOIN predictions ON event_id=predictions.id WHERE observer=? ORDER BY alerts.created_at DESC LIMIT ?", (observer, limit))]

    def recover_deliveries(self):
        with self.connection() as db:
            db.execute("UPDATE deliveries SET status='UNKNOWN',error='Interrupted send; outcome unknown' WHERE status='SENDING'")
            db.execute("UPDATE digest_deliveries SET status='UNKNOWN',error='Interrupted send; outcome unknown' WHERE status='SENDING'")

    def prediction_window(self,observer,detectors,start,end,*,starts_only=False):
        if not detectors:return []
        slots=','.join('?' for _ in detectors)
        times='start>? AND start<=?' if starts_only else 'end>? AND start<?'
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute(
                f"SELECT payload FROM predictions WHERE observer=? AND detector IN ({slots}) AND state NOT IN ('WITHDRAWN','EXPIRED') AND {times} ORDER BY priority DESC,start,id",
                (observer,*detectors,iso(start),iso(end)))]

    def digest(self,id):
        with self.connection() as db:
            row=db.execute('SELECT * FROM digest_deliveries WHERE id=?',(id,)).fetchone()
            return {**dict(row),'payload':json.loads(row['payload'])} if row else None

    def enqueue_digest(self,payload,observer,expires,now,*,status='PENDING'):
        with self.connection() as db:
            return bool(db.execute('''INSERT OR IGNORE INTO digest_deliveries
                (id,observer,mode,period,scheduled_at,expires_at,created_at,status,next_attempt,payload)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (payload['id'],observer,payload['mode'],payload['period'],payload['scheduled_at'],iso(expires),iso(now),status,iso(now),dumps(payload))).rowcount)

    def claim_digest(self,observer,now):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE digest_deliveries SET status='EXPIRED',error='Digest catch-up window ended' WHERE observer=? AND status IN ('PENDING','FAILED') AND expires_at<=?",(observer,iso(now)))
            row=db.execute("SELECT * FROM digest_deliveries WHERE observer=? AND status IN ('PENDING','FAILED') AND attempts<5 AND next_attempt<=? ORDER BY scheduled_at,id LIMIT 1",(observer,iso(now))).fetchone()
            if not row:return None
            db.execute("UPDATE digest_deliveries SET status='SENDING',attempts=attempts+1 WHERE id=? AND status IN ('PENDING','FAILED')",(row['id'],))
            return {**dict(row),'attempts':row['attempts']+1,'payload':json.loads(row['payload'])}

    def digest_result(self,id,status,now,attempts,*,error=None,remote_id=None,payload=None):
        retry=now+timedelta(seconds=min(3600,60*2**min(attempts,6)))
        with self.connection() as db:
            db.execute('UPDATE digest_deliveries SET status=?,next_attempt=?,error=?,remote_id=?,sent_at=? WHERE id=?',
                (status,iso(retry),error,remote_id,iso(now) if status=='SENT' else None,id))
            if payload is not None:db.execute('UPDATE digest_deliveries SET payload=? WHERE id=?',(dumps(payload),id))

    def retry_digest(self,id,observer,now):
        with self.connection() as db:
            return bool(db.execute("UPDATE digest_deliveries SET status='PENDING',attempts=0,next_attempt=?,error=NULL WHERE id=? AND observer=? AND expires_at>? AND status IN ('FAILED','UNKNOWN')",(iso(now),id,observer,iso(now))).rowcount)

    def digest_history(self,observer,mode,*,sent=False):
        with self.connection() as db:
            where=" AND status='SENT'" if sent else ''
            row=db.execute('SELECT id,period,status,scheduled_at,expires_at,created_at,attempts,next_attempt,error,sent_at FROM digest_deliveries WHERE observer=? AND mode=?'+where+' ORDER BY scheduled_at DESC LIMIT 1',(observer,mode)).fetchone()
            return dict(row) if row else None

    def claim_delivery(self, now: datetime, adapters: list[str], observer: str, detectors: list[str]) -> dict | None:
        if not adapters or not detectors:
            return None
        a, d = ",".join("?" for _ in adapters), ",".join("?" for _ in detectors)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(f"""SELECT deliveries.*,alerts.payload,predictions.payload AS current_prediction FROM deliveries
                JOIN alerts ON alert_id=alerts.id JOIN predictions ON event_id=predictions.id
                WHERE deliveries.status IN ('PENDING','FAILED') AND attempts<5 AND next_attempt<=?
                  AND predictions.end>? AND predictions.state!='WITHDRAWN' AND predictions.observer=?
                  AND adapter IN ({a}) AND predictions.detector IN ({d})
                ORDER BY deliveries.id LIMIT 1""", (iso(now), iso(now), observer, *adapters, *detectors)).fetchone()
            if not row:
                return None
            db.execute("UPDATE deliveries SET status='SENDING',attempts=attempts+1 WHERE id=?", (row["id"],))
            return {**dict(row), "attempts": row["attempts"] + 1, "payload": json.loads(row["payload"]),
                    "current_prediction": json.loads(row["current_prediction"])}

    def delivery_result(self, id: int, status: str, now: datetime, attempts: int, error: str | None = None,
                        remote_id: str | None = None):
        retry = now + timedelta(seconds=min(3600, 60 * 2 ** min(attempts, 6)))
        with self.connection() as db:
            db.execute("UPDATE deliveries SET status=?,next_attempt=?,error=?,remote_id=? WHERE id=?",
                       (status, iso(retry), error, remote_id, id))

    def retry_delivery(self, id: int, now: datetime) -> bool:
        with self.connection() as db:
            return bool(db.execute("UPDATE deliveries SET status='PENDING',attempts=0,next_attempt=?,error=NULL WHERE id=? AND status IN ('FAILED','UNKNOWN')",
                                   (iso(now), id)).rowcount)

    def prune(self, now: datetime, days: int):
        cutoff = iso(now - timedelta(days=days))
        with self.connection() as db:
            db.execute("DELETE FROM predictions WHERE end<?", (cutoff,))
            db.execute("DELETE FROM track_episodes WHERE last_position_at<?", (cutoff,))
            # Per-run/hour seeing queries are immutable but superseded frequently.
            # Keep a week of cache, independently of long-lived event history.
            cache_cutoff = iso(now - timedelta(days=7))
            db.execute("DELETE FROM provider_cache WHERE key LIKE 'eccc-point/%' AND (fetched_at<? OR (fetched_at IS NULL AND next_attempt<?))",
                       (cache_cutoff, cache_cutoff))
            db.execute("DELETE FROM provider_cache WHERE key LIKE 'detector-batch/%' AND fetched_at<?", (cache_cutoff,))
            db.execute("DELETE FROM provider_cache WHERE key LIKE 'hrrr-sparse/%' AND fetched_at<?", (cache_cutoff,))
            db.execute("DELETE FROM revisions WHERE evaluated_at<? AND id NOT IN (SELECT MAX(id) FROM revisions GROUP BY event_id)", (cutoff,))
