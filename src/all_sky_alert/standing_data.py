"""Bounded CC0 VRS standing-data cache; slow acquisition, prepared live lookups."""
import csv
from contextlib import closing
from datetime import timedelta
from email.utils import parsedate_to_datetime
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
from tempfile import TemporaryDirectory
from threading import Lock
import time
from zipfile import ZipFile

import httpx

from .adsb import text
from .domain import iso, parse
from .source_health import FreshnessPolicy, SourceHealth


SOURCE = "https://github.com/vradarserver/standing-data"
URL = "https://codeload.github.com/vradarserver/standing-data/zip/refs/heads/main"
RIGHTS = "CC0-1.0; https://github.com/vradarserver/standing-data/blob/main/LICENSE"
DOWNLOAD_LIMIT = 16*1024*1024
DATABASE_LIMIT = 64*1024*1024
EXPANDED_LIMIT = 64*1024*1024
ROW_LIMIT = 750000
AIRCRAFT_LIMIT = 200
ROUTE_NOTE = "Catalog callsign match; current flight endpoints unconfirmed"


def callsign_key(value):
    match = re.fullmatch(r"([A-Z]{3})([0-9]+[A-Z]{0,2})", (value or "").strip().upper())
    if not match:
        return None
    number = match[2].lstrip("0")
    if not number or not number[0].isdigit():
        number = "0"+number
    return match[1]+number if re.fullmatch(r"[0-9]{1,4}[A-Z]{0,2}", number) and len(number) <= 4 else None


def value(row, key, maximum=256):
    return text(row.get(key), maximum) or ""


def import_archive(archive, destination, metadata):
    """Stream CSV rows into a new bounded DB; never extract archive paths."""
    counts = {name: 0 for name in ("aircraft", "airlines", "airports", "model-type", "routes")}
    with ZipFile(archive) as zipped, closing(sqlite3.connect(destination)) as db:
        entries = zipped.infolist()
        if len(entries) > 10000 or sum(e.file_size for e in entries) > EXPANDED_LIMIT:
            raise ValueError("Oversized VRS archive")
        for entry in entries:
            if ".." in PurePosixPath(entry.filename).parts or entry.filename.startswith("/") or entry.flag_bits & 1:
                raise ValueError("Unsafe VRS archive")
        licenses = [e for e in entries if e.filename.count("/") == 1 and e.filename.endswith("/LICENSE")]
        if len(licenses) != 1 or licenses[0].file_size > 32000 or b"CC0 1.0 Universal" not in zipped.read(licenses[0]):
            raise ValueError("Expected VRS CC0 license")
        db.execute("PRAGMA journal_mode=OFF")  # Disposable build, never the published DB.
        db.execute("PRAGMA page_size=4096")
        db.execute(f"PRAGMA max_page_count={DATABASE_LIMIT//4096}")
        db.executescript("""
          CREATE TABLE aircraft(icao TEXT PRIMARY KEY,code TEXT,maker TEXT,model TEXT);
          CREATE TABLE airlines(code TEXT PRIMARY KEY,icao TEXT,name TEXT);
          CREATE INDEX airline_icao ON airlines(icao);
          CREATE TABLE airports(code TEXT PRIMARY KEY,name TEXT);
          CREATE TABLE models(code TEXT,maker TEXT,model TEXT,active INTEGER,PRIMARY KEY(code,maker,model));
          CREATE TABLE routes(callsign TEXT PRIMARY KEY,airline TEXT,origin TEXT,destination TEXT);
          CREATE TABLE metadata(payload TEXT NOT NULL);
          PRAGMA user_version=1;
        """)
        required = {"aircraft": {"ICAO", "ModelICAO", "Manufacturer", "Model"},
                    "airlines": {"Code", "ICAO", "Name"}, "airports": {"Code", "IATA", "Name"},
                    "model-type": {"ICAO", "Manufacturer", "Model", "IsActive"},
                    "routes": {"Callsign", "AirlineCode", "AirportCodes"}}
        rows = 0
        deadline = time.monotonic()+120
        for entry in entries:
            parts = PurePosixPath(entry.filename).parts
            if len(parts) < 4 or parts[1] not in counts or parts[2] != "schema-01" or not entry.filename.endswith(".csv"):
                continue
            group = parts[1]
            with zipped.open(entry) as stream:
                reader = csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig", newline=""))
                if not required[group] <= set(reader.fieldnames or ()):
                    raise ValueError("Unexpected VRS CSV schema")
                for row in reader:
                    rows += 1
                    if rows > ROW_LIMIT or (rows % 1000 == 0 and time.monotonic() > deadline):
                        raise ValueError("VRS import budget exceeded")
                    if None in row or any(row.get(k) is None for k in required[group]):
                        raise ValueError("Malformed VRS CSV row")
                    counts[group] += 1
                    if group == "aircraft":
                        icao = value(row, "ICAO").lower()
                        if re.fullmatch(r"[0-9a-f]{6}", icao):
                            db.execute("INSERT INTO aircraft VALUES(?,?,?,?)", (icao, value(row, "ModelICAO"), value(row, "Manufacturer"), value(row, "Model")))
                    elif group == "airlines":
                        db.execute("INSERT INTO airlines VALUES(?,?,?)", (value(row, "Code"), value(row, "ICAO"), value(row, "Name")))
                    elif group == "airports":
                        code = value(row, "Code")
                        display = (value(row, "IATA") or code)+" — "+value(row, "Name", 512)[:96]
                        db.execute("INSERT INTO airports VALUES(?,?)", (code, display))
                    elif group == "model-type":
                        db.execute("INSERT INTO models VALUES(?,?,?,?) ON CONFLICT(code,maker,model) DO UPDATE SET active=MAX(active,excluded.active)",
                                   (value(row, "ICAO"), value(row, "Manufacturer"), value(row, "Model"), int(value(row, "IsActive") == "1")))
                    else:
                        key = callsign_key(value(row, "Callsign"))
                        airports = value(row, "AirportCodes", 512).split("-")
                        # Never choose a current leg from a multi-stop catalog route.
                        origin, destination_code = airports if len(airports) == 2 and all(airports) else (None, None)
                        if key:
                            db.execute("INSERT INTO routes VALUES(?,?,?,?)", (key, value(row, "AirlineCode"), origin, destination_code))
        if not all(counts.values()):
            raise ValueError("Incomplete VRS archive")
        metadata = {**metadata, "counts": counts}
        db.execute("INSERT INTO metadata VALUES(?)", (json.dumps(metadata),))
        db.commit()
        if Path(destination).stat().st_size > DATABASE_LIMIT:
            raise ValueError("Oversized VRS database")
        return metadata


class StandingDataEnrichment:
    name = "aircraft.enrichment.vrs"

    def __init__(self, client, store, observer, settings, consumers, positions=None):
        self.client, self.store, self.settings, self.consumers = client, store, settings, consumers
        self.positions = positions
        self.path = Path(store.path).parent/"aircraft-standing.sqlite3"
        self.key = "vrs-standing/cc0-schema1/v1"
        self._lock, self._frame = Lock(), {}
        self.health = None

    def _connection(self):
        db = sqlite3.connect(self.path.resolve().as_uri()+"?mode=ro", uri=True, timeout=.5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA cache_size=-2048")
        if db.execute("PRAGMA user_version").fetchone()[0] != 1:
            db.close()
            raise ValueError("Unknown VRS cache schema")
        return db

    def _metadata(self, db):
        meta = json.loads(db.execute("SELECT payload FROM metadata").fetchone()[0])
        cache = self.store.cache(self.key)
        checked = cache["payload"] if cache and cache["payload"] else {}
        if checked.get("sha256") == meta["sha256"]:
            meta = {**meta, **checked}
        return meta

    def maintain(self, now, *, force=False):
        """Slow lane or locked CLI update; force bypasses age, never backoff."""
        if not self.settings["enabled"]:
            return {"status": "disabled"}
        try:
            meta = None
            try:
                with self._lock:
                    db = self._connection()
                    try:
                        meta = self._metadata(db)
                    finally:
                        db.close()
            except (sqlite3.Error, ValueError, KeyError, TypeError):
                pass
            cache = self.store.cache(self.key)
            if not force and meta and 0 <= (now-parse(meta["verified_at"])).total_seconds() < self.settings["refresh_days"]*86400:
                return {"status": "cached", **meta}
            if cache and cache["next_attempt"] and now < parse(cache["next_attempt"]):
                return {"status": "backoff", "next_attempt": cache["next_attempt"], "error": cache["error"]}
            headers = {"Accept-Encoding": "identity"}
            if meta and meta.get("etag"):
                headers["If-None-Match"] = meta["etag"]
            deadline = time.monotonic()+45
            with self.client.stream("GET", URL, headers=headers, timeout=httpx.Timeout(10, connect=3)) as response:
                if response.status_code == 304:
                    if not meta:
                        raise ValueError("VRS 304 without a valid cache")
                    metadata = {**meta, "verified_at": iso(now)}
                    self.store.cache_success(self.key, metadata, now)
                    return {"status": "not_modified", **metadata}
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError("Unexpected VRS status")
                if int(response.headers.get("Content-Length", "0")) > DOWNLOAD_LIMIT:
                    raise ValueError("Oversized VRS download")
                if shutil.disk_usage(self.path.parent).free < DATABASE_LIMIT+DOWNLOAD_LIMIT:
                    raise ValueError("Insufficient free space for VRS update")
                with TemporaryDirectory(prefix=".vrs-", dir=self.path.parent) as directory:
                    archive, database = Path(directory)/"catalog.zip", Path(directory)/"catalog.sqlite3"
                    digest, size = hashlib.sha256(), 0
                    with archive.open("wb") as file:
                        for chunk in response.iter_bytes(65536):
                            size += len(chunk)
                            if size > DOWNLOAD_LIMIT or time.monotonic() > deadline:
                                raise ValueError("VRS download budget exceeded")
                            digest.update(chunk)
                            file.write(chunk)
                    metadata = {"verified_at": iso(now), "downloaded_at": iso(now), "sha256": digest.hexdigest(),
                                "download_bytes": size, "etag": text(response.headers.get("ETag"), 512),
                                "upstream_last_modified": text(response.headers.get("Last-Modified"), 128)}
                    metadata = import_archive(archive, database, metadata)
                    with self._lock:
                        os.replace(database, self.path)
                        self.store.cache_success(self.key, metadata, now)
                    return {"status": "updated", **metadata}
        except Exception as exc:
            cache = self.store.cache(self.key)
            failures = (cache["failures"] if cache else 0)+1
            wait = min(86400, 3600*2**min(failures-1, 5))
            error = type(exc).__name__
            if isinstance(exc, httpx.HTTPStatusError):
                error += f" HTTP {exc.response.status_code}"
                header = exc.response.headers.get("Retry-After", "")
                try:
                    wait = max(wait, min(86400, int(header)))
                except ValueError:
                    try:
                        wait = max(wait, min(86400, (parsedate_to_datetime(header)-now).total_seconds()))
                    except (ValueError, TypeError):
                        pass
            retry_at = now+timedelta(seconds=wait)
            self.store.cache_failure(self.key, failures, retry_at, error)
            return {"status": "error", "error": error, "next_attempt": iso(retry_at)}

    def _lookup(self, db, state, meta, now):
        checked = parse(meta["verified_at"])
        age = (now-checked).total_seconds()
        static_end = checked+timedelta(days=self.settings["metadata_max_age_days"])
        route_end = checked+timedelta(days=self.settings["routes_max_age_days"])
        if age < 0 or now > static_end:
            return None
        row = {"icao24": state.icao24, "callsign": state.callsign, "updated_at": meta["verified_at"],
               "retrieved_at": meta["verified_at"], "valid_until": iso(static_end), "usable_until": iso(static_end),
               "age_seconds": round(age, 1), "status": "catalog", "source_name": "VRS standing data (CC0)",
               "source_url": SOURCE, "rights": RIGHTS, "catalog_sha256": meta["sha256"],
               "method": "CC0 standing catalog; individual row update times unknown; no current flight-plan claim",
               "component_deadlines": {"route": iso(route_end)}}
        aircraft = db.execute("SELECT code,maker,model FROM aircraft WHERE icao=?", (state.icao24,)).fetchone()
        if aircraft and (not state.type_code or aircraft["code"] == state.type_code.upper()) and aircraft["maker"] and aircraft["model"]:
            row.update(manufacturer=aircraft["maker"], base_model=aircraft["model"], type_name=aircraft["maker"]+" "+aircraft["model"])
        elif state.type_code:
            models = db.execute("SELECT DISTINCT maker,model FROM models WHERE code=? AND active=1 LIMIT 2", (state.type_code.upper(),)).fetchall()
            if len(models) == 1 and models[0]["maker"] and models[0]["model"]:
                row.update(manufacturer=models[0]["maker"], base_model=models[0]["model"], type_name=models[0]["maker"]+" "+models[0]["model"])
        key = callsign_key(state.callsign)
        if key:
            airlines = db.execute("SELECT name FROM airlines WHERE icao=? LIMIT 2", (key[:3],)).fetchall()
            if len(airlines) == 1 and airlines[0]["name"]:
                row["airline"] = airlines[0]["name"]
            if now <= route_end:
                route = db.execute("SELECT * FROM routes WHERE callsign=?", (key,)).fetchone()
                if route and route["origin"] and route["destination"]:
                    origin = db.execute("SELECT name FROM airports WHERE code=?", (route["origin"],)).fetchone()
                    destination = db.execute("SELECT name FROM airports WHERE code=?", (route["destination"],)).fetchone()
                    if origin and destination:
                        row.update(origin=origin["name"], destination=destination["name"], route_note=ROUTE_NOTE,
                                   route_catalog_callsign=key)
        return row if any(k in row for k in ("type_name", "airline", "origin")) else None

    def refresh(self, now):
        """Live lane: bounded local reads only; at() never touches disk/network."""
        frame, meta, error = {}, None, None
        if self.settings["enabled"]:
            try:
                with self._lock:
                    db = self._connection()
                    try:
                        meta = self._metadata(db)
                        states, _ = self.positions.snapshot(now) if self.positions else ((), None)
                        stale = self.positions.settings["stale_seconds"] if self.positions else 30
                        for state in states[:AIRCRAFT_LIMIT]:
                            if not re.fullmatch(r"[0-9a-f]{6}", state.icao24) or not 0 <= (now-parse(state.position_at)).total_seconds() <= stale:
                                continue
                            row = self._lookup(db, state, meta, now)
                            if row:
                                frame[(state.icao24, state.callsign, state.type_code)] = row
                    finally:
                        db.close()
            except Exception as exc:
                error = type(exc).__name__
        self._frame = frame  # Atomic publication; never mutate published rows.
        cache = self.store.cache(self.key)
        checked = parse(meta["verified_at"]) if meta else None
        policy = FreshnessPolicy(self.settings["refresh_days"]*86400, self.settings["refresh_days"]*86400,
                                 self.settings["metadata_max_age_days"]*86400)
        self.health = SourceHealth(self.name, "catalog", iso(now), policy, self.consumers,
            retrieved_at=iso(checked) if checked else None, available=meta is not None,
            next_refresh=cache["next_attempt"] if cache and cache["next_attempt"] else
                iso(checked+timedelta(days=self.settings["refresh_days"])) if checked else None,
            cached=True, error=error or (cache["error"] if cache else None),
            details={"disabled": not self.settings["enabled"], "freshness_basis": "retrieval", "non_gating": True,
                     "license": RIGHTS, "method": "Catalog validation age; row update times unknown",
                     "database_bytes": self.path.stat().st_size if meta else 0,
                     "counts": meta.get("counts", {}) if meta else {}})
        self.health.record(self.store)

    def at(self, state, now):
        row = self._frame.get((state.icao24, state.callsign, state.type_code))
        if not self.settings["enabled"] or not row or not parse(row["updated_at"]) <= now <= parse(row["usable_until"]):
            return None
        result = {**row, "component_deadlines": dict(row["component_deadlines"])}
        if now > parse(result["component_deadlines"]["route"]):
            for key in ("origin", "destination", "route_note", "route_catalog_callsign"):
                result.pop(key, None)
        return result
