# Optional aircraft enrichment cache

The public mode adds airline names, aircraft models and catalog route endpoints
without making requests for individual aircraft. It uses Virtual Radar Server's
[standing-data catalog](https://github.com/vradarserver/standing-data), released
under [CC0](https://github.com/vradarserver/standing-data/blob/main/LICENSE).
The ADS-B position feed and its independent position-age checks remain required.
Enrichment is optional and never gates aircraft detection or alerts.

## Enable and update

In Settings, enable **Aircraft labels / route enrichment**, choose **Public catalog
(weekly cache)** under **Aircraft enrichment source**, save and restart the service.
The ADS-B detector must also be enabled. Existing configurations default to the
licensed local-file mode, preserving their behavior; this feature stays disabled
until selected. Routine application updates still use the README's Pi updater.

For manual configuration, stop the service first and merge these keys into the
existing table, then validate and restart using the normal deployment procedure:

```toml
[providers.enrichment]
enabled = true
mode = "vrs"
refresh_days = 7
routes_max_age_days = 14
metadata_max_age_days = 30
```

The first slow worker cycle downloads the catalog. Live detections continue
without labels until it is available. The cache is `aircraft-standing.sqlite3`
beside the application database in the configured data directory. One cache serves
all tracked aircraft and survives restarts. There are no API keys, new Python
dependencies, scheduled tasks or per-aircraft HTTP requests.

After seven days the slow worker makes a conditional request using the stored
ETag. HTTP 304 advances the catalog validation time without rebuilding the DB;
HTTP 200 builds a new DB and atomically replaces the old one after validation.
Errors retain the last good DB, with persisted backoff starting at one hour and
capped at one day; Retry-After is respected within that cap. Source health records
failures, validation age, row counts and storage size. Disabling enrichment stops
its use and updates without deleting the cache or historical event records.

## Freshness and interpretation

Catalog validation time is separate from position time. Individual catalog row
update times are unavailable: a recent download does not prove that a route or
airframe record recently changed. Without successful revalidation, route matches
expire after 14 days, while airline/model labels remain usable for 30 days.
These configurable ages satisfy `refresh_days <= routes_max_age_days <=
metadata_max_age_days`; limits are 1–30 days for refresh, at most 30 days for routes
and 90 days for names. Routes expire independently of model/airline labels.

Route endpoints mean **catalog callsign match; current flight endpoints
unconfirmed**. Callsigns can be reused, schedules can change and flights can divert.
The cards, map and email include this qualification. Only single-leg routes with
both airports in the catalog are shown. Multi-stop routes and unknown airports
stay unknown; current legs are never inferred from heading or position. Only
unambiguous three-letter ICAO airline callsigns are matched, with leading numeric
zeros normalized. Two-letter airline prefixes and private tail numbers do not
become route guesses.

Airframe coverage is partial: the public catalog's
[aircraft documentation](https://github.com/vradarserver/standing-data/tree/main/aircraft/schema-01)
excludes the separate PlaneBase dataset and describes user-submitted records.
An individual aircraft model is used only when consistent with the feed's
reported type, if present. A type-code lookup with several active variants is not
resolved arbitrarily; the existing bounded FAA model-family reference can still
provide common names. Airline names use unambiguous catalog ICAO identifiers.
Only models, airline names and airports/routes are retained: no registration,
owner, serial number or flight history is imported. Existing feed privacy filters
for PIA/LADD/anonymized states still run before enrichment; no hidden identity is
reconstructed.

## Pi resource bounds

A credential-free check of the upstream snapshot during implementation downloaded
7,144,322 bytes (6.8 MiB) and produced a 33,136,640-byte SQLite file (31.6 MiB):
17,139 aircraft rows, 5,965 airlines, 34,128 airports, 11,084 model rows and 629,710
route rows before filtering. These are measurements of that snapshot, not a
promise about future catalog size or update cadence.

The implementation enforces these fixed limits:

| Resource | Bound |
|---|---|
| Compressed download | 16 MiB |
| Expanded archive contents | 64 MiB; at most 10,000 ZIP entries |
| Retained database | 64 MiB |
| CSV rows across tables | 750,000 |
| Download / import time budget | 45 / 120 seconds, plus bounded socket waits |
| Prepared live aircraft lookups | 200 fresh aircraft per refresh |
| SQLite read page cache | 2 MiB |

The archive is streamed to temporary storage and CSV rows stream into SQLite;
the full catalog is not loaded into Python memory. Normal live refreshes perform
indexed local lookups, and presentation reads only the small prepared frame.
Bulk work runs in the existing slow lane, leaving the live worker separate.
During replacement, old DB + new DB + archive can occupy at most approximately
144 MiB; a download requires at least 80 MiB free beyond existing files. Temporary
archives/builds are removed afterward, including on failure. No raw archive or
unbounded catalog history is retained. A future catalog exceeding these limits
fails safely and keeps the previous DB subject to the same expiry rules.

## Compatibility and rollback

`mode = "local"` retains the existing user-licensed JSON adapter, its path schema,
hourly file cache and independent record timestamps. Its `cache_seconds` and
`max_age_hours` settings do not control the public mode. See
[prediction guide](prediction-guide.md) for the local-file contract.
To roll back public enrichment, select the local mode or disable enrichment, save
and restart. No main-database schema migration or route-data replay is required.

The previous ADSBDB restriction remains relevant to **that different source**:
its [README](https://github.com/mrjackwills/adsbdb) requires separate permission to
incorporate its route data. This implementation does not copy that database or
call that API; it imports only the separately published CC0 VRS catalog.
