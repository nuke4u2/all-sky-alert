# Phase 2 implementation and verification

Completed 2026-10-08 America/New_York (checks after midnight UTC are dated October 9).
Read [source research](source-capabilities.md), [extension design](phase2-architecture.md)
and [architecture](architecture.md) for contracts and authoritative links.

## Baseline and scope

Before product edits: **84 tests passed**, one existing Starlette TestClient/httpx
deprecation warning. Existing source, detector, atmosphere/seeing, SQLite/outbox,
Gmail, HTML, config and development-handoff contracts were inspected. The directory
is not a git checkout; no commit, push, history rewrite or git initialization occurred.

Two new detector families are implemented: **observer-local solar/lunar eclipses**
and **OVATION aurora horizon opportunities**. They complement the existing planetary
and sunset modules while exercising shared ephemeris, shared weather, long-range
searches and live forecasts. No new dependencies, paid APIs, credentials, GPU or
runtime LLM components were added. Referenced project/library implementation code
was not copied. The existing MIT Astronomy Engine dependency supplies calculations.

## Architecture changes and reuse

- Source registrations own acquisition/defaults/validation; detector registrations
  declare dependencies, profiles, cadence/lane and local-ephemeris use. One shared
  provider instance is constructed for each required domain source.
- SourceHealth stores retrieval, observation/model time, valid extent, cache/error,
  cadence, expiry policy and actual consumers. Views age timestamps at read time.
  Local ephemeris health identifies library/version rather than HTTP/observation age.
- One process runs independent serial slow/live lanes and bounded delivery. Each
  live detector has its own due time; one fast consumer cannot force every other
  consumer to run at its cadence. Slow geometry batches persist across restarts.
  Atmospheric/policy reassessment continues inside geometry cache intervals.
- Shared weather/SWPC publish coherent frames; SWPC serializes concurrent refreshes.
  Snapshot reads do no I/O. Weather and seeing acquisition serve declared consumers.
- Candidate distinguishes scheduled, opportunity and transient semantics, with
  optional scientific input validity. Profiles own observing-input freshness and
  deadlines. Core policy does not impose weather on every possible event.
- Exceptional importance bypasses poor/unknown weather, never expired scientific
  predictions. Queued delivery checks expiry even if evaluations stop. Seeing
  expiry affects its consumers, not unrelated sunset/eclipse/aurora profiles.
- SQLite schema 2 adds source health without changing existing tables/identities.
  Durable unique event/milestone keys remain the UI/email deduplication ledger.

Eclipse calculations reuse the current local ephemeris and common weather. Aurora
reuses local Sun/Moon/illumination geometry, common cloud/transparency and one
OVATION provider. Kp/Bz feeds are not downloaded without a real consumer. The other
families' shared orbit, microphysics and catalogue capabilities remain documented
acceptance gates rather than empty implementations.

## Working behavior

Eclipses support local partial/annular/total solar phases, obscuration and local
solar contacts; penumbral/partial/total lunar phases are clipped to local altitude
intervals. Maximum below the horizon is distinguished from visible other phases.
Penumbral-only local visibility respects the configured preference, including when
the global eclipse is total. Exceptional-interest events survive poor weather.
Solar-viewing instructions are present in cards, details and email MIME.

Aurora projects geographic grid cells onto a spherical Earth with an explicit
emitting-height range. It distinguishes horizon, low-sky and high/overhead
opportunities; undefined overhead azimuth is not reported as a northern bearing.
Sun altitude, Moon interference, cloud and transparency contribute separately.
Native NOAA index and our heuristic score are not calibrated visual probabilities.
Source/forecast times remain separate. Observer/local observing-night identity
allows rolling revisions without new alerts for every forecast. At most one
outlook and one active milestone are emitted per night.

UI has Now, Upcoming, configurable Recent (default two hours), source health and
details. Years are displayed for long-range events. Expired scientific inputs are
identified on retained forecasts. JSON keeps the existing inclusive upcoming list
and adds active/source-health data. No UI route acquires data or sends email.

## Source policy and configuration

| Source | Retrieval policy | Freshness/qualification |
|---|---|---|
| Open-Meteo | Default 60min combined observer forecast cache | Retrieval freshness is explicitly a proxy because upstream initialization is unavailable; stale fallback up to 6h; routine profiles require fresh inputs |
| ECCC RDPS seeing | Metadata cache 60min; immutable observer/run/hour point cache; bounded request budget/backoff | Native categories 0 masked, 1–5 ordinal; run age limited to 18h by default; stale native input cannot qualify its consuming profile |
| NOAA OVATION | Default 300s cache; persisted backoff/Retry-After; bounded response/full-grid validation | Observation age >30min stale, >60min invalid; future observations/expired application window rejected. Application window is forecast ±20min, not a NOAA duration field. Scientific validity ends at the earlier of observation+30min and window end |
| Local ephemeris | No network; bounded position cache and persisted detector batches | Astronomy Engine 2.1.19/versioned calculation validity; no fabricated observation age |

New tables: detectors.eclipses, detectors.aurora and providers.swpc. Geometry
interval_seconds is also configurable for planetary/sunset. The example enables
the new detectors; omitted detector tables default to disabled for compatibility.
Only the missing three tables were appended to this workspace's config.toml;
observer, existing preferences and Gmail settings were retained. No secret file
was inspected. Restart to apply configuration changes.

## Tests and operational checks

**Final full suite: 137 passed**, up from 84 (**53 additional cases**), with the
same existing TestClient deprecation warning. After final presentation edits,
**42 targeted eclipse/aurora/web tests passed**. compileall succeeded and pip check
reported no broken requirements. No lint/type-check configuration existed, so no
new toolchain was imposed.

New tests cover realistic synthetic full OVATION grids, invalid/null/duplicate
cells and timestamps, response bounds, old data despite HTTP 200, disabled source,
cache/restart/429 backoff, concurrent shared refresh, horizon/bearing/height
geometry, both hemispheres, darkness/Moon/cloud screening, and stable nightly IDs.
Eclipse fixtures cover Dallas totality against an independent USNO reference
(all contacts within 60s allowing UT1/UTC and model differences), NYC partial,
Albuquerque annular, lunar rise/set clipping, total phases below the horizon,
optional penumbral visibility and safety presentation/MIME.

Core tests cover per-detector cadence, geometry reassessment/restart/settings
invalidation, malformed cached time/duplicate IDs, shared-provider failure
isolation, concurrent lane independence/non-overlap, background startup/shutdown,
stalled-worker freshness, queued expiry, transient dedup across revisions/restart,
profile-specific expiry, non-weather profiles, exceptional scientific expiry and
additive schema migration. Existing tests retain UI escaping/host/CSP and mocked
Gmail adapter/ambiguous-delivery coverage. Normal tests block HTTP and requests.

Public smoke checks used **separate test storage**, the demonstration Greenwich
location, adapters=[], Gmail disabled and notifications disabled:

- At 2026-10-09 03:00:46 UTC, 38 upcoming predictions: 26 planetary, 8 sunset,
  3 eclipses and 1 aurora opportunity. Counts vary by time/location/activity.
- All four sources healthy. First evaluation 3.966s; cached repeat 0.241s.
  Requests across both: one Open-Meteo, one OVATION, eight GeoMet (metadata plus
  seven distinct seeing hours). The repeat made **no additional requests**.
- Actual Uvicorn localhost TCP smoke: home/health and all 38 detail pages returned
  200; server shut down cleanly. No production database or OAuth data was used.
- Docker image built on Linux/amd64 as all-sky-alert:phase2. Offline container
  smoke used --network none, no host mounts, temporary storage and synthetic SWPC;
  all four detector families, SQLite and HTML/JSON routes passed as non-root UID 10001.
  Actual Synology/ARM hardware and long-running operation remain untested.

Research probes also checked public CelesTrak, ADSB.lol, OpenSky, Launch Library,
MPC, JPL SBDB and HRRR indexes. Airplanes.live returned 403 from this environment;
that is not a global outage claim. No authenticated Space-Track access, route data
copying, national GRIB download or live email occurred. Probe/smoke artifacts are
under ignored test-artifacts/; durable source findings are in the matrix.

## Exact Windows commands

From E:\AI\AgentProjects\all-sky-alert with the existing environment/config:

```powershell
.venv\Scripts\python.exe -m all_sky_alert --config config.toml serve
```

Open **http://127.0.0.1:8008/**; stop with Ctrl+C. Use one server worker, no reload.
The service starts evaluation automatically. A cold live forecast can initially
have unknown weather until a subsequent live cycle reads the shared weather frame.

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m compileall -q src tests
.venv\Scripts\python.exe -m pip check
```

Initial environment creation, one-shot evaluation, preview-first Gmail OAuth/test
commands and NAS volume/secret configuration are in [README](../README.md).
Gmail remains optional and uses the existing OAuth adapter; no credentials are
needed for these new detectors. Never send live mail during normal tests.

## Deferred families and next phase

| Family | Remaining work / reason |
|---|---|
| ADS-B interesting aircraft | Track episodes, positional age, configured interest rules and projected-proximity uncertainty. ADSB.lol is a viable evaluated source; ODbL obligations and separate route-enrichment permissions need deliberate implementation. ADSBDB route data cannot silently be incorporated |
| Ice optics | Narrow HRRR vertical-profile extraction/decoding, local Sun/optical geometry and validation. Confirmed ice mass/number fields do not establish crystal habit/orientation; a cloud-layer threshold is insufficient |
| ISS Sun/Moon transits | Shared OMM/SGP4 plus aligned precise disc ephemerides, orbital-age/manoeuvre gates, footprint/near-miss fixtures and subsecond root refinement. Solver precision must not be confused with orbit certainty |
| Launches/reentries | Stable schedules plus actual trajectories/uncertainty. Schedule alone is not local visibility. Optional Space-Track credentials/terms only when approved; no generic reliable fuel-dump timetable established |
| Galilean/GRS | Validated moon/shadow contact geometry and simultaneous-event grouping; shared planetary assessment. GRS needs dated configurable longitude/drift/provenance because no dependable structured current feed was verified |
| Lunar planetary occultations | Precise topocentric limb/separation roots, independent ingress/egress fixtures, local altitude/twilight and limb context; not planetary 15-minute sampling |
| Bright comets | Orbit/solution catalogue pipeline, local geometry, uncertain predicted magnitude versus actual measured photometry. MPC/SBDB access alone is not observed brightness or local observability |

No fake/empty modules or tests claiming these families were added. Directional
horizon clouds, extinction/light-pollution calibration, aurora storm-episode IDs,
automatic model fusion, precision uncertainty and proximity-adaptive scheduling
are also deferred. A future expensive live calculation needs its own bounded
executor/lane; the current live forecast lane is serial. Weather-triggered source
composition can evolve when its first real microphysics consumer exists.

Recommended next phase: **shared OMM/SGP4 and precision Sun/Moon transit validation**,
then ADS-B track episodes. Both build on the present source, freshness, cadence,
scientific-validity and deduplication contracts. Continue calibrating seeing and
aurora heuristics against observations. Use [HANDOFF](../HANDOFF.md) for bounded
local-agent work; architecture/credentials/push/commit permissions remain restricted.

## Change inventory (git diff substitute)

Git diff is unavailable because there is no repository metadata. No commit/push.

- New product modules: source_health.py, sources.py, swpc.py, aurora.py, eclipse.py.
- Updated product: astronomy, domain, detectors, assessment, registry, config,
  providers, canadian, service, storage, policy, notifications, web and CLI;
  index/detail templates and CSS. No dependency or deployment-file changes.
- Tests: three new modules (eclipses, SWPC/aurora, cadence/health), two fixtures;
  existing detector/web expectations updated for the UI and opportunity semantics.
- Documentation: source matrix, Phase 2 design and this report; README,
  architecture/research/extension review, AGENTS and HANDOFF updated.
- Configuration: example expanded; only missing new tables appended to local config.
- Ignored test artifacts: public probes, smoke scripts/reports and private temporary
  databases. Production data, OAuth files and unrelated projects were not accessed.
