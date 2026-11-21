# Bounded developer handoff

Primary deployment is now native Raspberry Pi OS Lite 64-bit/Trixie with a system
Python venv and systemd; Synology/Docker is deprecated and unmaintained. Read
[Pi operation](docs/raspberry-pi.md), [camera design](docs/camera-architecture.md)
and [verification](docs/pi-verification.md) for camera/deployment handoffs.
Camera Module 3 is **General Pi Camera / Timelapse**, not the deferred ZWO All-Sky
Camera. Camera tasks may touch explicitly allowed `camera/` modules and UI/tests,
but must keep one hardware owner, bounded previews/commands, offline fake-backend
tests and prediction/provider isolation. Saved TOML requires restart; explicit
camera session controls do not reload config. Never give a local handoff actual
config, camera captures, secrets, mail or permission to install/enable systemd.

Copy this template into a task document (e.g. docs/handoffs/task-name.md). Supply
explicit paths and acceptance criteria. Completion returns a diff and report for
Codex/Astra review; no commits, pushes or merges by default. This is a development
convention, not an application runtime feature.

For source/detector work, include the relevant rows of
[source-capabilities.md](docs/source-capabilities.md) and
[Phase 2 contracts](docs/phase2-architecture.md).
[Phase 3 contracts](docs/phase3-architecture.md) apply to orbital/aircraft work.
Keep normal tests offline, source HTTP stop/epoch/position gates intact, and
user-licensed enrichment optional. Numeric transit tolerance is not a physical
uncertainty guarantee. Do not expand into generic launch/reentry visibility.
Identify its worker lane,
source timestamps/expiry, complete reconciliation scope and stable episode identity.
Provider handoffs must specify response-size limits, cache keys, data validation,
backoff, atomic snapshot publication and offline fixtures. Do not interpret an
HTTP 200 as fresh observations, a native category as physical seeing, or a catalog
prediction as a confirmed sighting/photometric measurement. A bounded handoff
does not authorize live smoke tests, secrets, paid services or architecture changes.

```markdown
# Task: <bounded objective>

## Outcome
<observable behavior and specific acceptance criteria>

## Architecture context
<where the change sits; link docs/architecture.md and relevant contracts>

## Allowed files
- <explicit modules/tests/docs that may be changed>

## Protected files and access
- <explicit files/modules that must not be changed>
- No secrets/, data/, OAuth client/token files, Gmail/GitHub credentials,
  unrelated directories, pushes, merges, commits or history rewriting.

## Invariants
- <stable identity, UTC/time ordering, unknown handling, score separation, etc.>
- No changes to core contracts/architecture unless specifically authorized here.
- No runtime LLM dependencies or orchestration infrastructure.

## Required verification
- <targeted offline tests and meaningful failure cases>
- Linux: .venv/bin/python -m pytest
- Linux: .venv/bin/python -m compileall -q src tests
- Windows development: .venv\Scripts\python.exe -m pytest
- Windows development: .venv\Scripts\python.exe -m compileall -q src tests

## Sources and context
- <specific docs, source URLs, fixture definitions, prior decisions>
- Credential-free/read-only web research is allowed; no live API/mail tests.

## Deliverable
<implemented diff, test results, limitations, concise notes for architectural review>
```

Example bounded task: add independent tests for the sunset profile's missing
cloud-layer behavior and 100% low-cloud obstruction. Allowed: tests/test_domain_assessment.py
only. Protected: all src/, configuration, deployment and core docs. Invariants:
no live network, intrinsic interest untouched, unknown metrics never treated as
zero cloud. Deliver a passing test diff and report any discovered source-code bug
to Codex/Astra rather than expanding the scope without authorization.

Phase 4 context: include `docs/phase4-architecture.md` and
`docs/phase4-operation.md` for map/health/settings/optics tasks. Actual config is
protected unless explicitly allowed. Use private TOML/SQLite fixtures for settings
work. Browser QA is optional `.[qa]` with `scripts/phase4_browser_smoke.py`;
external tiles are disabled. Live HRRR probes require explicit task scope and must
honor byte/run limits. Never supply production OAuth or database access to a local
agent. A bounded task cannot change LAN/proxy trust, notification identity or
scientific freshness contracts without Codex/Astra review.

Notification work also reads `docs/gmail-modes.md`. Preserve separate immediate
milestone/daily/weekly identities, DST/catch-up behavior and UNKNOWN semantics.
Use fake adapters; do not read production predictions for a bounded handoff unless
the task explicitly authorizes it. Previews must not initialize Service, migrate
the database, acquire data or access OAuth credentials.

## Current aircraft enrichment implementation

The optional CC0 VRS standing-data cache is implemented; see
[operation and resource limits](docs/aircraft-enrichment.md). Existing configs
retain `providers.enrichment.mode="local"` and enrichment remains disabled by
default. Settings exposes a public-catalog choice, enable toggle and validation
age controls; saving still requires restart. No actual deployment config changed.

`standing_data.py` streams a bounded weekly conditional archive into a reduced
SQLite database beside the main application database. A credential-free static
catalog/import check produced a 6.8 MiB download and 31.6 MiB database. Fixed caps
are 16 MiB download, 64 MiB DB/expanded archive and 750k CSV rows. Individual row
update times are unknown; successful validation is not a live flight observation.
ADSBDB's separate data restriction remains unchanged and its API/data are unused.

`sources.py` injects the existing ADS-B snapshot provider. `Service` runs the
optional provider `maintain(now)` hook only in the serial slow lane; live refresh
does bounded indexed local reads and publishes an immutable frame. Presentation
and detector `at()` reads have no I/O. Failed builds keep the old database with
persisted backoff. Route expiry (14 days by default) is independent of model/
airline expiry (30 days). Catalog endpoints are explicitly unconfirmed across
cards/map/email; multistop/ambiguous matches stay unknown. PIA/LADD/anonymized
states still pass through existing exclusion before lookup. No registration,
owner or serial-number columns are retained. No dependencies/schema migration
were added, and no Pi service was restarted or deployed in this task.

Verification on Windows: 75 targeted tests passed, including synthetic archive,
failure/backoff/expiry, settings, detector/mail and slow/live maintenance tests.
Full suite: 455 passed, 2 existing failures in
`test_local_times.py::test_service_mail_and_ui_use_configured_timezone` (exact body
tag) and `test_swpc_aurora.py::test_aurora_composed_ui_source_expiry_and_disabled_provider`
(obsolete Now heading). These dashboard assertions also failed before this task.
Tests block live network and use private scratch databases. Raspberry Pi runtime/
performance acceptance remains pending; use the documented updater before enabling
the public cache. Pre-existing Jupiter/dashboard edits in the working tree were
preserved and are outside this aircraft task.


## Aircraft map tooltip sizing

Aircraft hover labels now use a dedicated `aircraft-map-tooltip` Leaflet class
with explicit 240px width, a smaller viewport-relative maximum on phones, 14px
text and normal word wrapping. This prevents the absolute tooltip box from
shrinking to a character column under the page-wide `overflow-wrap:anywhere`.
The vendor stylesheet stays unchanged; the scoped rule has enough specificity
when Leaflet CSS loads after the main stylesheet.

`scripts/aircraft_ui_browser_smoke.py` now checks actual hover label dimensions,
text size and overflow for both synthetic aircraft at 360/390/768/1280px widths,
and supports `--output` to keep screenshots/reports in the shared test workspace.
Verification: 55 targeted aircraft/map/enrichment tests passed; JavaScript syntax,
Python script compilation and `git diff --check` passed. Browser layout execution
remains unverified: sandbox Edge launch failed with access errors, and the approved
escalated attempt failed to create the process security environment. No browser
or dependency installation was attempted. Next verification: run the fixture-only
browser smoke script with an available browser and inspect `tooltip-390.png` and
`tooltip-1280.png`. No production config or service was changed.

## Enrichment command-line loading

`enrichment-update [--force]` now loads or validates the public catalog in the
configured service data directory. README and aircraft-enrichment.md document
the stop/update/start sequence, source-setting prerequisite and JSON output.
The command acquires ServiceLock before creating the Store or HTTP client and
does not construct Service, run detectors, acquire positions/weather, read OAuth
files, send mail, open a camera or edit configuration. It can prefill the cache
with the ADS-B detector disabled, but enrichment must explicitly be enabled in
public mode. No new configuration fields or dependencies were needed.

StandingDataEnrichment.maintain returns an outcome for operator reporting;
normal slow-lane callers continue to ignore its return value. `force=True`
bypasses only the weekly age check. ETag/304 validation and persisted error/
Retry-After backoff remain in effect. CLI failures/backoff exit 1 even if an old
cache exists; a valid retained cache is never presented as a successful failed
update. Output includes cache path/size, validation/download times and row counts
on success, or error/next_attempt on failure.

Verification: 94 offline tests passed across command loading/force/304/failure/
backoff/lock/settings cases, catalog, aircraft/map, service/storage, Gmail config
and Phase 3 integration. CLI help, Python compilation and diff checks passed.
No live catalog download, production config access or Pi service change was
performed for this command-line addition.
