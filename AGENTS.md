# Development contracts

Read README.md and docs/architecture.md before changing code. Core abstractions
remain under Codex/Astra architectural authority. A bounded Qwen/OpenClaw handoff
must use HANDOFF.md and name allowed files plus contracts; it does not authorize
architecture changes outside that scope.

- Detectors emit Candidate/DetectionBatch, acquire no atmosphere directly and send
  no notifications. Inject new domain-data providers into detector constructors.
- Shared atmospheric acquisition/caching belongs in providers.py and canadian.py;
  independent event profiles belong in assessment.py. Unknown is not clear. Never report an
  uncalibrated proxy as physical seeing or a calibrated probability.
- ECCC seeing_index is a native category: 0 is cloud masked; 1–5 is ordinal.
  Preserve model run/valid/retrieval times; do not convert indices to exact arcseconds.
  Conditions.at() performs no I/O; optional prepare() acquires needed samples.
  Stale inputs used by a profile cannot qualify routine alerts; unrelated sources
  must not veto profiles that do not consume them.
  Input validity deadlines also belong to consuming profiles, not all conditions.
  Candidate scientific validity is separate: exceptional weather bypass must
  never permit expired prediction inputs, including queued watch deliveries.
  Profiles own input usability/expiry and declare acquisition needs. Do not
  impose weather or a weather deadline on every event from core policy/service.
  Preserve the distinction between NOAA's native OVATION grid proxy, our
  emitting-height scenarios and the uncalibrated observer opportunity score.
- Intrinsic interest, quality, occurrence confidence, assessment confidence and
  lifecycle/delivery state must remain distinct.
- Persist stable observer/occurrence identity and unique alert milestone keys.
  Gmail UNKNOWN outcomes must never be automatically retried.
- Use aware UTC internally; local dates/timezones only define observing scopes and
  presentation. Include provenance and method for every external/derived metric.
  Local ephemerides have library/version/coverage, not a live observation-age TTL.
  Eclipse contacts/visible phases must be clipped to the observer horizon.
- Never read secrets/, data/, OAuth token/client files, credentials, unrelated
  projects or personal directories during a bounded local agent task. Use test
  fixtures and private temporary databases; no live mail in tests.
- Local agents may read/write allowed repo files, run tests/build/compile checks,
  inspect git status/diff/log, and use credential-free read-only web research.
  They may not use Gmail/GitHub credentials, push, merge, commit by default,
  rewrite history or change architecture without explicit authorization.
- Do not create git commits unless explicitly requested. Do not spawn additional
  agents unless the user explicitly authorizes them for the task.
- Run `.venv/bin/python -m pytest` and relevant targeted tests after core
  changes on Linux; Windows development uses `.venv\Scripts\python.exe -m pytest`.
  Tests block network; optional live evaluation requires explicit task
  scope. Do not add runtime LLM or automated agent orchestration dependencies.

Composition is service.py plus registry.py (detector factories/defaults/validation).
sources.py constructs shared domain-data providers only for declared consumers.
SourceHealth policies separate retrieval, observation/issue and valid time.
Phase 3: GP is modern OMM, UTC/TEME/WGS72 SGP4, WGS84 observer. EPOCH age
is distinct from HTTP age; stale elements cannot authorize transit alerts.
Non-200 CelesTrak responses persist an HTTP stop; never auto-reset it. Packaged
DE421 and built-in timescale are offline, versioned assets. Solver tolerance is
not actual transit timing uncertainty; GP has no covariance. Reuse eclipse solar
safety in transit UI/email. Center-track contacts are not silhouette contacts.
Aircraft episode continuity persists in schema 3 separately from position validity.
Use actual seen_pos/time_position age; no stale current claims or route/airport
guessing. PIA/LADD/anonymized states must not be reconstructed. Enrichment is an
optional dependency, independently aged and non-gating, with explicit data rights.
Clock-only scheduled milestones must not overwrite concurrent detector updates.
Focused live JSON is read-only; JavaScript renders untrusted text with textContent.
Phase 4: transient map trails are bounded by time/aircraft/points and independent
of durable alert episodes. Observed breadcrumbs, inferred constant-motion paths
and licensed route enrichment remain visibly separate. No browser upstream feed
calls or map-triggered detector evaluation. Keep Leaflet's vendored BSD notice.
Network binds/peers/hosts/proxies are explicit; never introduce wildcard trust.
Settings is a fixed operational allowlist with full schema validation, bounded
JSON, exact same-origin CSRF, revision guard and atomic directory-level replace.
Saved config requires restart. Stop before CLI/manual config maintenance. Do not
edit actual config in a bounded handoff without explicit task authorization.
Gmail routing derives from registry families; historic milestones never replay.
Optics are uncalibrated opportunities. Preserve crystal/geometry/obstruction
separation. HRRR has numeric-parameter/units/run/level checks, byte budgets and
immutable extracted-run caching. NCCICE is per kg, not per cubic metre. Never
invent ice habit/orientation or download full HRRR files. Coarse fallback survives
optional microphysics failure; current inputs must still meet profile validity.
Slow geometry caches are persisted; live detectors always read current snapshots.
Worker lanes are serial individually; snapshot reads must be I/O-free/thread-safe.
Treat published source and conditions snapshots as read-only.
Normal UI requests never initiate data acquisition or detector calculation.
Keep detector/provider/profile registrations
explicit until a real plugin distribution requirement justifies discovery.
