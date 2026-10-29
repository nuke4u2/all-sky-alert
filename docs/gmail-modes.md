# Gmail notification modes

Design recorded 2026-10-27 before implementation. Baseline: 277 offline tests pass
(19.79 seconds, seven existing dependency warnings). Preserve the modular monolith,
Pi camera/deployment work, prediction envelopes and local alert milestones.

Immediate Gmail adds a channel-specific intrinsic-interest floor (85 by default)
to existing lifecycle, quality/confidence and exceptional-weather-bypass policy.
It does not alter local milestones or interest/quality scores. Routing remains
registry-family based. Enabling a channel/mode never backfills old milestones.

Daily and weekly summaries are separate durable SQLite jobs, not event alerts.
Schema 4 additively adds a digest outbox with unique observer/mode/calendar-period
identity, attempts, schedule/expiry, snapshot, remote acceptance and safe errors.
The existing delivery lane creates/claims these jobs; no extra scheduler or detector
I/O occurs during formatting. Atomic claims and unique keys prevent overlap and
restart duplicates. Gmail uses the existing adapter/OAuth/send/error semantics.
Safe failures retry at most five times; ambiguous sends and interrupted SENDING
jobs become UNKNOWN and require explicit acknowledged retry. No exactly-once
claim is possible across Gmail acceptance and local commit.

Daily selects remaining eligible events overlapping the observer's local calendar
day, including windows begun the previous day. Ordinary events use existing
interest/quality/confidence/freshness policy; exceptional events retain weather
bypass and are explained separately. Weekly selects starts strictly beyond the
configured seven-calendar-day offset through 90 days by default, at interest >=70.
It requires occurrence confidence/scientific validity, not weather availability.
Only detector registrations declaring a meaningful long-range geometry horizon
participate. Currently eclipses already calculate 730 days independently of the
seven-day general horizon. No ADS-B/aurora/weather/GP horizon is expanded; missing
future predictions are explained, never invented. More long-range families can
declare their own horizon setting when actually implemented.

Schedules use observer IANA timezone, not machine timezone. Fall-back uses the
first occurrence of a repeated local time; spring gaps move to the first valid
minute after the gap. Daily identity is local YYYY-MM-DD; weekly identity is ISO
year/week of the configured weekday slot. Changing weekday within that ISO week
does not produce a second weekly job. Defaults are 07:00 daily and Sunday 18:00
weekly. Catch-up is bounded to four hours daily (and before that day's end) and
24 hours weekly. No missed-period backlog. Empty periods are recorded without
mail; first-generation work waits while normal slow evaluation is in progress.
Snapshots are rebuilt from persisted current predictions before a safe send/retry,
so withdrawn, expired or no-longer-eligible items cannot survive in delayed mail.

Each mode has independent registry-family selection. Legacy gmail.event_types
migrates into missing mode selections while explicit per-mode selections win.
Model defaults keep scheduled modes disabled for existing installations; this task
explicitly enables both schedules in actual/example config while preserving the
existing global Gmail enablement, recipient, OAuth paths and all other preferences.
Settings retains bounded JSON, CSRF, validation, revision guards, atomic writes
and restart semantics. Optional base_url is explicit and validated, never guessed.

Preview CLI uses read-only SQLite and the exact MIME renderer, without constructing
Service, reading credentials, evaluating detectors, sending mail or upgrading the
production database. Actual persisted predictions may be read only for the previews
explicitly requested in this task. Normal tests use isolated private databases.


## Operation and configuration

The actual and example TOML now remove the legacy top-level Gmail event_types and
add these 18 keys. Existing observer/timezone, destination, OAuth paths, global
Gmail enablement, detector/provider/camera/network and other personal values are
unchanged. Actual Gmail was already enabled and remains so; example Gmail remains
disabled. Both configs explicitly enable the three mode switches:

| Key | Supplied config value |
| --- | --- |
| gmail.base_url | "" (no URL guessed; configure a credential-free HTTP(S) URL using an exact service.allowed_hosts entry) |
| gmail.immediate.enabled | true |
| gmail.immediate.minimum_interest | 85 |
| gmail.immediate.event_types | ["*"], migrated from legacy selection |
| gmail.daily_digest.enabled | true |
| gmail.daily_digest.time | "07:00", observer timezone |
| gmail.daily_digest.event_types | ["*"], migrated |
| gmail.daily_digest.catch_up_hours | 4 |
| gmail.daily_digest.max_events | 20 |
| gmail.weekly_digest.enabled | true |
| gmail.weekly_digest.weekday | "Sunday" (full English weekday) |
| gmail.weekly_digest.time | "18:00", observer timezone |
| gmail.weekly_digest.minimum_interest | 70 |
| gmail.weekly_digest.start_days_ahead | 7 (strictly beyond the local-calendar offset from the slot) |
| gmail.weekly_digest.end_days_ahead | 90 (inclusive start-time upper bound) |
| gmail.weekly_digest.event_types | ["*"], migrated; only capable families participate |
| gmail.weekly_digest.catch_up_hours | 24 |
| gmail.weekly_digest.max_events | 20 |

All mode family selectors derive from registry.DETECTORS. ["*"] includes future
eligible families; explicit IDs exclude future families; [] includes none.
Weekly "all" does not grant a short-range detector long-range capability. A new
long-range module declares its calendar-day geometry setting through
DetectorRegistration.long_range_horizon. That is metadata describing the module's
existing calculation, not an instruction to stretch its data horizon.

Model defaults keep both scheduled modes disabled for configurations that have not
opted in. This task explicitly opted the supplied configs in. Legacy files still
load; missing per-mode selectors inherit the old selection. Explicit selectors win.
config-sync persists the canonical form and removes the obsolete key atomically:

~~~powershell
.venv\Scripts\python.exe -m all_sky_alert --config config.toml config-sync
~~~

Settings has separate immediate/daily/weekly panels, times, weekday, thresholds,
bounds, catch-up limits, message limits and family checkboxes. The timezone is
visible. Shared minimum confidence and exceptional weather-bypass interest remain
the existing notification-policy fields and are editable in Alert policy; no
duplicate per-mode quality/confidence policy was introduced.
Network, paths, recipient, URL and OAuth contents remain protected from
web editing. Save reports restart required. The home and Settings pages show next
slot/pending status, last Gmail acceptance and latest period status/reference.
The read-only /api/notifications endpoint exposes the same information without
credentials, recipient or email content. Digest failures appear in this status
and separate digest health rows; they do not mark unrelated data providers bad.

Stop/restart the normal service to apply the new config and add schema 4. Existing
event/alert/delivery identities are preserved. Back up private state with SQLite
backup or a complete stopped database, including WAL state as appropriate. No
production database migration or mail was performed during this development task.

### Selection and duplication semantics

Daily ordinary events must meet the existing per-kind notification interest,
quality, confidence and consumed-input freshness policy. Exceptional events retain
the established weather bypass. All items must have valid scientific inputs and
not be withdrawn/ended. Daily covers the local day and includes currently relevant
cross-midnight windows; events already ended at generation time are omitted.

Weekly requires the mode's interest floor, per-kind occurrence confidence and
scientific validity, with no weather-quality gate. It uses only persisted rows of
enabled, selected, long-range-capable modules and caps each by its configured
horizon. Current eclipse geometry is independent of service.prediction_days=7 and
already uses 730 days. Missing far-ahead weather stays unknown; stale quality is
not presented as a current numerical forecast. No opposition, comet, occultation,
launch or long-range ISS prediction was added by this notification change.

One event may appear in successive weekly summaries while it remains in range,
in daily summaries for each local day its window overlaps, and in legitimate
watch/ready/imminent immediate mails. These are intentional different
communications. There is at most one scheduled job per observer/mode/calendar
period, and one immediate job per existing event milestone. Configuration changes
do not replay existing milestones or completed/empty digest periods. A newly
enabled schedule can catch up its current period only within its configured window.

Period states are PENDING, SENDING, SENT, FAILED, UNKNOWN, EMPTY, EXPIRED and CANCELED.
EMPTY consumes that period without sending; newly discovered ordinary events later
that day wait for normal future schedules or remain in the UI. Late live events
may need a lower immediate floor if notification is wanted; no morning digest can
promise evening aurora or aircraft discovery. Setting the global notification or
Gmail switch off pauses delivery. A disabled digest mode cancels queued work when
delivery resumes. Mode/family policy is checked before sending.

Scheduled anchors and expiry of already materialized jobs stay fixed when the
configured time/weekday changes; new periods use the new schedule. Current selectors,
bounds and forecast content are rechecked before a safe send/retry. The original
job identity/Message-ID stays fixed. Spring gaps shift forward to the first valid
minute; repeated times use the first occurrence, with the same calendar identity
covering both folds. Day windows can be 23 or 25 hours. Weekly identity uses the
ISO year/week of the configured slot, not UTC week or a rolling seven-day timer.
Changing weekday within that ISO week does not create another job; moving to a
different ISO week naturally creates a new period.

Catch-up daily is capped both by the configured elapsed-hour delay and local
midnight. Weekly uses its elapsed-hour delay, including across DST. A startup
after the window records EXPIRED and sends no missed backlog. Current slow
evaluation is allowed to finish rather than generating partial first-run content;
an old interrupted worker record does not indefinitely block summaries. Formatting
never requests a fresh evaluation, weather or ephemeris computation.

Safe pre-acceptance failures retry at most five times, with backoff of 120, 240,
480, 960 and 1920 seconds, bounded by period expiry. Ambiguous timeout/5xx,
unexpected adapter result and interrupted SENDING recover as UNKNOWN. Gmail
acceptance is separate from actual inbox receipt. The unavoidable send/commit
window means exactly-once remote delivery cannot be guaranteed. To retry an
unexpired failed/unknown digest deliberately, stop the service and use its status
reference:

~~~powershell
.venv\Scripts\python.exe -m all_sky_alert --config config.toml retry-digest digest-IDENTITY --acknowledge-possible-duplicate
~~~

This queues only; it does not send immediately. The next delivery cycle rechecks
config, forecasts and expiry. Inspect Gmail first; acknowledged UNKNOWN retry may
duplicate a remote acceptance. No automatic UNKNOWN retry exists.

### Safe previews

~~~powershell
.venv\Scripts\python.exe -m all_sky_alert --config config.toml preview-daily-digest
.venv\Scripts\python.exe -m all_sky_alert --config config.toml preview-weekly-digest
.venv\Scripts\python.exe -m all_sky_alert --config config.toml preview-weekly-digest --date 2026-11-01 --format eml
~~~

On the Pi replace the interpreter with .venv/bin/python. Daily defaults to today's
observer-local date. Weekly defaults to the latest slot still within catch-up,
otherwise the next slot. --date explicitly chooses a reference date; weekly bounds
are measured from that date's delivery time. Previews reflect current persisted
data/config, not predictions that might arrive before the future slot.

The same structured builder and MIME renderer serve preview and delivery. Default
text shows subject/body; --format eml exposes complete MIME. Body is plain text,
not executable HTML; external strings never become headers, and embedded line
controls are collapsed. Solar safety is retained. Content is compact, prioritized
and grouped into high-interest, best-observing, atmospheric/daytime and exceptional
poor/unknown-weather sections, with peak/local times, independent scores/confidence,
seeing/cloud where known, provider provenance/licence credits and optional explicitly configured UI links. Weekly order follows intrinsic interest; daily uses presentation priority.

Preview opens SQLite in mode=ro/query_only without Store initialization, schema
upgrade, Service construction, camera, upstream requests, OAuth reads, outbox jobs
or sending. The production schema-3 database can be previewed before restart.
SQLite may still use its normal WAL/shared-memory coordination. No database
contents are changed. There is no digest send-test flag; the existing explicit
test-email --send mechanism is unchanged and was not invoked.

## Verification and limits

- Baseline: 277 passed in 19.79s; final: 313 passed in 21.66s.
- 36 new offline parameterized cases in tests/test_digest_policy.py cover threshold
  composition, daily/weekly ranges, horizon/interest/category selection, exceptional
  poor weather, cross-midnight and 23/25-hour days, missing/repeated DST times,
  duplicate claims, restart/catch-up, safe failure/attempt ceiling, UNKNOWN/manual
  retry, expiration/cancellation, migrations, Settings/status, external text,
  solar safety, provider credits, stale-seeing labels and read-only schema-3 CLI preview.
- Existing routing tests were updated for the intentional per-mode configuration
  and schema-4 additive upgrade. Normal tests block network and use fake adapters.
  Seven existing Starlette/httpx and Skyfield/NumPy deprecation warnings remain.
- Headless Edge 156 fixture-only QA: 55 page checks at 360/390/430/768/1280 pixels,
  no page-wide horizontal overflow or browser errors. Independent mode selections,
  interest/time/weekday changes, atomic save and restart banner passed. External
  tiles and mail disabled; no production state was used.
- Actual read-only previews ran on 2026-10-27 late evening America/New_York:
  daily Oct 27 had no remaining eligible rows; next weekly Nov 1 covered starts
  strictly after Nov 8 18:00 EST through Jan 30 2027 18:00 EST, also empty.
  No rows were fabricated, no sources fetched, no schema migration and no mail.
  Both read-only previews were repeated after local midnight on 2026-10-28;
  today's daily and the same next-weekly window were also empty. Date-specific
  UTF-8 preview artifacts retain these actual results.
- Actual/example migration added 18 Gmail keys and removed gmail.event_types;
  existing values, including actual enabled Gmail and private paths, were verified
  unchanged. Private config comparison and test/preview artifacts are under
  test-artifacts/gmail-modes/ (gitignored).
- Deployment/systemd/Pi-camera changes already present in the working tree were
  preserved. This task changed notification code/config/UI/docs/tests only; it did
  not install/restart a Pi service, touch OAuth files, commit or push.
- Pi hardware/native scheduling were not exercised. This phase used Windows
  offline integration/browser checks; it did not revive deprecated containers.

Remaining limits: only eclipses currently declare useful long-range geometry;
other meaningful astronomy needs actual detectors, not mail-only placeholders.
No live test email, hardware deployment or delivery acceptance is claimed. Preview
content can change as predictions update; new sends/retries reselect current
content. Calendar job identity and frozen timing prevent duplicate periods, not
arbitrary manual UNKNOWN retransmission. Trusted-LAN Settings still requires
restart and has no app authentication. Empty periods are intentionally quiet.

Recommended next step: review preview content and schedule preferences, back up
state and restart the normal service. Then verify period status on the Pi without
sending a test message; add another credible long-range astronomical detector when
its source/geometry/uncertainty contract is ready.

