# All Sky Alert

A local modular sky prediction service. It calculates Jupiter, Saturn and Mars
observing windows, local sunset opportunities, observer-local solar/lunar eclipses,
NOAA OVATION-based aurora horizon opportunities, visible ISS passes, and live
ADS-B aircraft overhead/approaching episodes. Experimental ISS Sun/Moon center-track
transits use refined local geometry. Ice-crystal optics opportunities use shared
cloud layers and local Sun/Moon geometry, with optional sparse HRRR microphysics.
The phone-friendly UI includes a live aircraft map, multi-select prediction filters,
a capability-aware health stoplight, and bounded Settings controls.
Configured NORAD objects can use the same
sunlit pass calculation, with brightness/detectability explicitly unknown.
Visual astronomical modules share one atmospheric forecast; aircraft existence
does not require weather.
Predictions, forecast revisions, alert milestones and email delivery attempts
survive restarts in SQLite.

**Canadian astronomical seeing forecasts are available.** The optional ECCC RDPS
provider supplies the native 1–5 seeing index used by Clear Dark Sky's Canadian
forecasting approach, directly from MSC GeoMet. It is enabled in the example
configuration. Categories remain categories; seeing arcseconds remain unknown.
The UI labels a wind-based fallback when the Canadian forecast is unavailable.
Sunset color potential is an
experimental cloud-layer heuristic. Geometry is calculated locally even when the
weather API is unavailable. See [architecture](docs/architecture.md),
[research](docs/research.md), [current source matrix](docs/source-capabilities.md),
[Phase 2 design](docs/phase2-architecture.md) and [verification](docs/phase2-verification.md).
Aurora visibility bands are explicitly heuristic, not calibrated probabilities.
Phase 3 claims and tests are documented in [design](docs/phase3-architecture.md)
and [verification](docs/phase3-verification.md). Launch/plume/reentry visibility,
occultations, comets and Jovian/GRS remain deferred. Phase 4 extensions and limits
are recorded in [design](docs/phase4-architecture.md),
[operation](docs/phase4-operation.md) and [verification](docs/phase4-verification.md).

## Raspberry Pi native deployment (recommended)

Deploy natively on **allsky-pi4**: Raspberry Pi OS Lite 64-bit / Debian 13 Trixie,
system Python venv, one unprivileged process and systemd. Docker/Synology deployment
is deprecated and currently unmaintained. The complete [Pi installation guide](docs/raspberry-pi.md)
provides apt packages, clean-install commands, private-state migration, LAN setup,
camera diagnostics, foreground acceptance and the reviewed systemd installation.

**General Pi Camera / Timelapse** adds a separate Camera tab for Camera Module 3:
demand-only low-rate MJPEG, supported autofocus/manual lens controls, full-resolution
JPEG captures and a configurable timelapse. Camera hardware has one owner thread,
independent of prediction workers. It is disabled by default and requires apt
Picamera2/libcamera on the Pi. This is separate from a future **ZWO All-Sky Camera**;
no image analysis or ZWO support is implemented. [Verification and limitations](docs/pi-verification.md)
distinguish offline tests from outstanding hardware acceptance.

The expected trusted LAN URL is **http://allsky-pi4.local:8008/** after explicit
network configuration. Start with Gmail disabled and run `check` before `serve`.
Config saves require restart; Camera session Start/Stop works immediately.

## Windows development (supported secondary path)

Run these exact PowerShell commands from the project directory. Python 3.12 or
newer must be installed; the Windows `py` launcher avoids Store alias problems.

```powershell
Set-Location E:\AI\AgentProjects\all-sky-alert
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -c constraints.txt -e ".[dev,hrrr]"
if (-not (Test-Path -LiteralPath config.toml)) {
  Copy-Item -LiteralPath config.example.toml -Destination config.toml
}
notepad config.toml
```

Replace the **demonstration Greenwich coordinates and timezone** with your observer
latitude/longitude, elevation and IANA timezone (e.g. `America/New_York`). No personal
location is assumed. Longitude west of Greenwich is negative. Data paths resolve
relative to the configuration file, so the working directory does not determine
where your database is stored. Leave Gmail disabled initially.

```powershell
.venv\Scripts\python.exe -m all_sky_alert --config config.toml serve
```

Open **http://127.0.0.1:8008/**. Independent slow and live workers evaluate immediately;
the slow lane wakes every five minutes; the live lane wakes at the fastest enabled
detector cadence (15 seconds for aircraft, 300 seconds for aurora). Sources retain
independent cache/backoff. Delivery and scheduled milestones have their own bounded
15-second loop, without rediscovering geometry. Aircraft refresh through focused
JSON every 15 seconds; only the home page reloads every five minutes. Settings,
map and detail pages retain their state. Stop with Ctrl+C. “Now” and “Upcoming” events
are ordered by priority; recent history defaults to events ending in the previous
two hours. Recent history fills naturally as the service runs: no fabricated past
predictions are added on first launch. Detail pages expose reasons, atmospheric
metrics, geometry, sources, alerts and recorded revisions. Eclipse details show
local contacts/visible phases and solar-viewing safety. Aurora details explain
bearing, elevation-height scenarios, darkness and Moon interference. Source health
ages observation/model timestamps on every page/API read, even if a worker stalls.
Existing configurations do not automatically enable the new detectors: add their
tables below or use `config-sync`. The example enables them; preserve your observer
settings when upgrading. This phase updated the existing development configuration
explicitly: **LAN and atmospheric optics are enabled; Gmail remains disabled**.

The recommended Pi LAN URL is **http://allsky-pi4.local:8008/**. Localhost remains
**http://127.0.0.1:8008/**. See [Pi setup](docs/raspberry-pi.md) and
[LAN configuration and security](docs/phase4-operation.md#lan-access).
There is no authentication in this trusted-home-LAN phase. Any trusted LAN user
can use Settings; use an authenticated TLS reverse proxy before broader access.

**Settings** saves a fixed list of validated toggles/thresholds to the actual TOML
file atomically, preserving comments, observer values and secret paths. It reports
**Saved — restart required**; stop with Ctrl+C and run `serve` again. Changes are
audited by key name only. Observer/network/source URLs, paths and credentials are
edited locally, never through the web form. Stop the service before CLI/manual
configuration maintenance. To add newly introduced defaults without erasing values:

```powershell
.venv\Scripts\python.exe -m all_sky_alert --config config.toml config-sync
```

Omitted new detector defaults remain disabled; synchronization does not silently
opt users into detectors. The explicit development enablement above is separate.
Filters use registry-derived checkboxes and URL state; All/None affects Upcoming
and Now, leaving history, storage and alert policies intact. The health stoplight
uses source-specific freshness and required/optional dependency roles: green is
sufficiently fresh, yellow is partial loss/fallback, red is a required capability
or central worker/database failure, gray is initialization.

**Live aircraft map:** self-hosted Leaflet 1.9.4, optional OpenStreetMap tiles, pan,
zoom, touch selection and observer/radius overlay. The browser polls our normalized
API every 15 seconds; it never downloads ADS-B feeds. Routine aircraft appear even
when no alert is warranted. Solid trails are observed points retained for ten
minutes (150 aircraft, 80 points each by default); dashed continuations are bounded
constant-motion estimates, not filed routes. Restart clears trails. Altitude source,
position age, range and heading are labeled. Routes/types use independently aged
licensed enrichment only; unknown routes remain unknown. Online tiles reveal the
viewed region to the tile provider; disable them in Settings for a blank base map.
See [tile policy and map bounds](docs/phase4-operation.md#aircraft-map).

**Atmospheric optics:** 22° halos, sun dogs, circumzenithal/tangent arcs, low-Sun
pillars, lunar halos and separate cold/weak-wind/ice-saturation diamond-dust
conditions. These are coarse opportunity forecasts with crystal habit/orientation
unknown, not calibrated probabilities. Geometry, crystal suitability and low-cloud
obstruction are explained separately. Direct HRRR is optional and CONUS-only;
selected GRIB records are range-downloaded and cached per model run, never entire
continental files. The development config enables it after a successful Windows
decoder probe; the portable example keeps it opt-in. Install the `hrrr` extra if
needed, then enable `providers.hrrr.enabled`. Missing microphysics falls back to
shared coarse weather. Defaults select hybrid levels 20/30 at +3/+6 hours, a
32 MB/run budget, six-hourly runs and hourly cache checks. These sparse samples
cover only a small part of the 48-hour optics horizon; absence at sampled levels
does not establish absence throughout the atmosphere.

To evaluate once without starting the UI (stop the running service first):

```powershell
.venv\Scripts\python.exe -m all_sky_alert --config config.toml evaluate
```

This uses your enabled notification methods; with the supplied configuration it
only records local UI alerts. The OS lock prevents simultaneous coordinators on
the same data directory. Use **one worker**, without uvicorn `--reload`.

## Configuration

`config.example.toml` documents all current settings. Unknown keys are rejected
to catch misspelled settings. Restart after edits. Tables isolate concerns:

| Table | Settings |
| --- | --- |
| observer | latitude, longitude, elevation_m, timezone |
| service | data_dir, evaluation interval, recent_hours, prediction_days, retention_days, allowed_hosts |
| detectors.planetary | enabled, geometry interval_seconds, bodies, minimum altitude, maximum Sun altitude, aperture_mm |
| detectors.sunset | enabled, geometry interval_seconds |
| detectors.eclipses | enabled, horizon_days, interval_seconds, solar/lunar/penumbral, minimum_altitude_deg |
| detectors.aurora | enabled, interval_seconds, minimum_intensity, minimum_horizon_elevation_deg, maximum_sun_altitude_deg, emission height range |
| providers.swpc | enabled, cache_seconds, stale_after_minutes, invalid_after_minutes |
| detectors.iss | enabled, interval_seconds, horizon_hours, catalog_ids, minimum altitude, maximum Sun altitude |
| detectors.iss_transits | enabled, interval_seconds, horizon_hours, Sun/Moon selection, minimum altitude |
| detectors.adsb | enabled, interval_seconds, elevation entry/exit, bounded projection, episode gap, range, interest lists/squawks |
| providers.orbits | enabled, catalog_ids, two-hour minimum cache, epoch warning/stale/invalid, tighter transit gates |
| providers.adsb | enabled, radius_nm, cache_seconds, position stale/invalid thresholds |
| providers.enrichment | enabled, user-licensed JSON path, long cache, independent metadata age |
| atmosphere | enabled, cache_minutes, max_stale_hours, forecast_days |
| atmosphere.canadian | enabled, metadata cache/stale limits, maximum model-run age and grid-point distance |
| notifications | enabled, interest/quality/confidence thresholds, exceptional-interest bypass, milestone lead times |
| notifications.overrides."event.kind" | per-kind policy overrides using the same keys |
| gmail | enabled, destination, client_secret path, token path |
| camera | enabled, picamera2 backend/index, data-relative capture directory, preview toggle/size/fps |
| camera.timelapse | startup enabled, interval_seconds (10–604800), data-relative directory |

The common prediction horizon can extend to 365 days; the eclipse module has its
own horizon (default 730 days, maximum 3650), avoiding years of nightly planet sampling.
Atmospheric coverage is independently limited to at most 16 days. The default
seven-day common horizon suits nightly opportunities.
Long horizons cost more local calculations and usually have unknown weather.
Increasing prediction_days does not cause detectors to make weather calls.
Changing observer location creates a new identity scope; old predictions stay in
storage until retention pruning but are not mixed into the new observer's UI.
The additive SQLite schema upgrade retains existing predictions, revisions and
alert keys. Back up the database before deploying an upgrade.

### Eclipses and aurora

For an existing configuration, add:

```toml
[detectors.eclipses]
enabled = true
horizon_days = 730

[detectors.aurora]
enabled = true

[providers.swpc]
enabled = true
cache_seconds = 300
stale_after_minutes = 30
invalid_after_minutes = 60
```

No new dependencies or credentials are required. Other settings inherit the
documented example defaults. Slow geometry is reused across restarts: planetary
hourly, sunset daily and eclipse every six hours by default. Atmosphere and alert
policy are reassessed each slow cycle. Live aurora geometry always uses the current
provider snapshot; unused sources are not fetched.

Eclipses calculate local solar type/obscuration/contacts and lunar phases, clip to
the airless body-center horizon, and show when maximum/totality is below the local
horizon. Terrain, lunar limb topography and near-horizon refraction are unresolved;
contact display has one-minute resolution. Penumbral-only visible phases are
excluded by default. Total/annular events retain exceptional interest even under
clouds; configure per-kind policies if desired. To get a much earlier exceptional
watch, set `notifications.watch_lead_hours` (maximum 10000, about 416 days).
**Solar viewing requires appropriate protection and securely fitted front-aperture
filters for telescopes/binoculars**; follow the linked NASA instructions.

Aurora projects geographic OVATION cells onto a spherical Earth and an uncertain
110–250 km emitting-height range, then assesses darkness, Moon, shared cloud and
transparency proxies. It uses both observation and forecast time, not just HTTP
success. The application uses a ±20-minute opportunity window around the supplied
forecast time; NOAA does not provide this interval. Data older than 30 minutes can
be inspected with degraded confidence but cannot qualify routine alerts; older
than 60 minutes or beyond the opportunity window cannot generate new predictions.
Forecast lead varies; the product can use Kp fallback, which its JSON does not
identify. No unused solar-wind/Kp downloads or Kp-to-elevation mappings are made.
Horizon cloud paths, extinction, light pollution and emission-height certainty
remain unresolved. A quiet or daylight sky can correctly produce no aurora event.
Historical predictions remain available after a source failure and expire by time.

### Phase 3: ISS and live aircraft

Upgrade packages, **preserving your existing config.toml**:

```powershell
.venv\Scripts\python.exe -m pip install -c constraints.txt -e ".[dev]"
```

New detector tables omitted from existing configurations stay disabled. This task
preserves your location and existing Gmail settings while synchronizing the local config. To enable Phase 3,
add these tables if absent; copy further preferences from the example:

```toml
[detectors.iss]
enabled = true

[detectors.iss_transits]
enabled = true # Experimental nominal geometry; disable if unwanted.

[detectors.adsb]
enabled = true

[notifications.overrides."iss.pass"]
ready_lead_hours = 12
imminent_lead_minutes = 5

[notifications.overrides."aircraft.episode"]
minimum_interest = 70

[notifications.overrides."iss.transit.sun"]
ready_lead_hours = 12
imminent_lead_minutes = 5

[notifications.overrides."iss.transit.moon"]
ready_lead_hours = 12
imminent_lead_minutes = 5
```

Start with `.venv\Scripts\python.exe -m all_sky_alert --config config.toml serve`
and open **http://127.0.0.1:8008/**. The necessary no-credential sources default to
enabled when used. Do not duplicate tables already present. Source settings,
additional kind policies and all defaults are in `config.example.toml`.

**ISS passes:** local WGS72 SGP4/OMM propagation, WGS84 observer, Sun/Earth shadow
and darkness gates. Visible segments must exceed the configured elevation while
sunlit and the observer Sun is below -6° by default. Rise/maximum/set refer to the
geometric horizon; visible segment and peak are separately recorded. Clouds and
transparency affect quality, not whether geometry is retained. No precise magnitude
is claimed. CelesTrak GP is shared between pass/transit consumers and cached at
least two hours, including across restarts. Epoch warning/stale/invalid defaults
are 24/48/96 hours; stale geometry is inspectable but cannot qualify routine alerts.
The target's propagation age is also recorded. A successful HTTP download never
refreshes the physical epoch.

**ISS transits:** five-second closest-approach bracketing followed by numerical
minimum/contact refinement to 0.001 seconds. Contacts describe the **nominal center
track through a spherical apparent disk**, not the ISS silhouette or validated
real-world timing. Solar/lunar topocentric apparent directions use packaged JPL
DE421 with light time/aberration; Moon transits can exist in daylight. Element age
must be <=12 hours now and <=24 hours at target by default. GP supplies no covariance;
manoeuvres, drag, Earth orientation, terrain, limb topography and attitude can
shift or remove the transit. Subsecond numerical precision is not subsecond
physical accuracy. Details display contact seconds, duration, position angle,
range, angular speed, epoch, asset identity and unknown timing uncertainty.
**Never look at the Sun through unfiltered optics. Telescopes/binoculars require
appropriate, securely fitted front-aperture solar filters**; the existing eclipse
safety language appears in transit detail pages and emails.

Skyfield, sgp4, NumPy and the packaged `skyfield-data` DE421 asset are installed
with the application. No kernel or IERS file is downloaded at service runtime.
The kernel checksum/version and conservative 1900–2053 coverage are recorded.
Update dependencies periodically to maintain built-in leap/TT/UT1 tables.

To follow another already-cataloged spacecraft, add its integer NORAD ID to **both**
`detectors.iss.catalog_ids` and `providers.orbits.catalog_ids` (maximum ten).
Its event is a sunlit geometric opportunity with unknown brightness. Newly launched
objects need a known catalog ID and available GP; there is no automatic catalog
discovery. This does not predict a launch plume, venting or a glowing reentry.

**Aircraft:** ADSB.lol observer-radius requests every 15 seconds by default;
individual position timestamp = feed `now` minus `seen_pos`. Entry elevation 30°,
exit region 20°, position expiry 30 seconds and episode absence timeout 180 seconds
are separate concepts. Constant-motion estimates are anchored at position time
and bounded to 180 seconds, with unknown turns/climbs/errors. Geometric altitude
is preferred; barometric approximation is labeled. Bearing/elevation/range, speed,
track, vertical rate, sky motion and bounded closest approach are shown. Known
callsigns/type codes are used; unknown fields and routes degrade gracefully.
PIA/LADD-marked/anonymized states are skipped; withheld data are not reconstructed.
Aircraft do not require weather; their quality score describes sky-position
relevance and does not imply confirmed visual detection.

One persisted ICAO/episode identity survives short gaps and restarts. At most one
approaching `outlook` and one `active` alert occur per qualifying episode; polls
create revisions, not repeated mail. Exact callsign/type/ICAO interests, low height,
high elevation and reported emergency squawks can raise intrinsic interest.
Feed emergency reports are unverified, not official advisories. See provider health
for coverage gaps/backoff. The focused live view removes expired inputs even if
the worker stalls; retained details/history show the recorded state with timestamps.

**Optional route/type enrichment:** disabled by default. No unrestricted live route
source was verified; ADSBDB route data require permission and are not incorporated.
You may supply a JSON file containing records you own or have permission to use:

```json
[
  {
    "icao24": "abc123", "callsign": "JBU123", "type_name": "Airbus A320",
    "origin": "JFK", "destination": "BOS",
    "updated_at": "2026-10-09T12:00:00Z", "valid_until": "2026-10-09T18:00:00Z",
    "source_name": "My licensed source", "source_url": "https://example.org/",
    "rights": "My permission to use this record"
  }
]
```

This is a **synthetic schema example**, not real flight information. Set
`providers.enrichment.enabled=true` and `path` to your own file, relative to config.
Routes require exact aircraft **and** callsign binding plus both endpoints. Metadata
must have provenance/rights, positive validity <=7 days and age <=24 hours by default.
Type-only records may omit callsign/endpoints. Metadata refreshes hourly, independent
of positions. Missing/malformed/expired enrichment never suppresses positional
events and is reported as route unknown. Source health/record age are independent.

**Explicit public connectivity smoke (no mail or credentials):**

```powershell
.venv\Scripts\python.exe -m all_sky_alert --config config.toml source-smoke
```

Uses separate `test-artifacts/phase3-source-smoke/` storage and respects persistent
GP cadence/stop state. `--source orbits` or `--source adsb` narrows the check.
It is never called by pytest. ADSB.lol uses ODbL 1.0; retain attribution and review
share-alike obligations before publishing a derived database. Its limits are
dynamic and API access may change. CelesTrak's [usage policy](https://celestrak.org/usage-policy.php)
requires automated clients to stop on non-200 HTTP responses. The stop latch
persists; investigate the health error, then stop the service and explicitly run:

```powershell
.venv\Scripts\python.exe -m all_sky_alert --config config.toml reset-orbit-source
```

This only clears the production cache's HTTP stop latch; it sends no mail and makes
no request. Backoff remains. Smoke storage has its own latch. There is no automatic
reset at restart. Network/validation errors wait at least the GP cache interval.

### Canadian seeing

Enable or disable the optional source independently in `config.toml`:

```toml
[atmosphere.canadian]
enabled = true
cache_minutes = 60
max_stale_hours = 6
max_run_age_hours = 18
max_grid_distance_km = 25
```

Restart the service with the same `serve` command above. No additional packages,
API key or decoder installation is needed. Omitted tables default to disabled,
so older configurations retain their behavior. The main `atmosphere.enabled`
switch disables both weather and Canadian seeing acquisition.

RDPS runs at 00/06/12/18 UTC, with hourly seeing coverage up to 84 hours after
initialization (the remaining horizon shrinks as the run ages). Coverage is
regional; Canadian seeing for unsupported locations/hours remains unknown. The UI displays **Very bad
(1/5)** through **Excellent (5/5)**. **Cloud masked (0)** means turbulence quality
is unknown, not a seeing category worse than 1. Planetary quality uses this
index ahead of the wind proxy, with cloud cover and altitude still assessed
separately. Stale seeing can be inspected but cannot qualify routine alerts.
Sunset quality ignores seeing. Exceptional intrinsic-interest alerts retain
their weather bypass.

Event details include model initialization, sample valid time, retrieval time,
grid location/distance, freshness and source. Model categories do not establish
an exact resolution for an 8-inch telescope. The forecast was calibrated with
28–35 cm telescopes; aperture, target altitude, terrain, cooling, collimation and
local turbulence affect what actually resolves. No automatic aperture correction
or index-to-arcseconds conversion is applied. See the
[implementation and source notes](docs/canadian-seeing.md).

Routine events alert once at `ready` and once at `imminent` if quality and
confidence qualify. Exceptional events additionally support a long-lead `watch`
and bypass poor/unknown weather. Milestones persist across restarts and forecast
oscillations. Scheduled events create no new notification milestones once their
window has started. Existing queued jobs can retry while the event is ongoing and
their inputs still qualify.
Transient aurora forecasts can alert once at `outlook` and once at `active` per
observer/local observing night; rolling forecast times update the same object.
This deliberate nightly cap avoids model-refresh notification storms; separate
bursts within a night do not get independent alerts yet.
`notifications.enabled=false` stops new
alerts and email dispatch; predictions remain visible. `gmail.enabled=false`
disables just email. `gmail.event_types=["*"]` selects all registered current and
future detector families; an explicit list such as `["eclipses", "iss_transits"]`
excludes other and future families; `[]` selects none. Settings provides these
choices. Routing never changes detectors, predictions or local milestones, and
enabling a category does not replay historical alerts. Pending mail for newly
excluded categories is canceled on restart. UI alert records are the channel-independent deduplication
ledger. Enabling Gmail later affects new milestones rather than replaying old ones.

## Gmail OAuth (optional)

No existing project or secret directories were inspected. This is a standard
independent Desktop OAuth setup using only the `gmail.send` scope.

1. Install the extra: `.venv\Scripts\python.exe -m pip install -c constraints.txt -e ".[gmail]"`.
2. In Google Cloud create a project, enable **Gmail API**, configure OAuth consent,
   and add your Gmail account as a test user if the app is in Testing.
3. Create an OAuth **Desktop app** client. Download its JSON into
   `secrets/client_secret.json`, or use a private directory outside the repo and
   set its absolute path in your configuration.
4. Set your single destination email address in `[gmail]`. Stop the service during
   authorization/testing so it cannot refresh the token concurrently.
5. Authorize locally; this opens Google's consent flow and **sends no email**:

```powershell
.venv\Scripts\python.exe -m all_sky_alert --config config.toml gmail-authorize
```

The refresh token is stored in `secrets/gmail_token.json` by default. Restrict that
directory to your Windows account; `.gitignore` excludes secrets, token/client
JSON files, local configuration and data. If choosing an arbitrary in-repo secret
filename, put it under `secrets/`. Never include secrets in handoffs or container
images. On a NAS mount tokens separately with service-user-only permissions;
the token directory must permit atomic refresh writes.

Preview a test email without reading credentials or sending:

```powershell
.venv\Scripts\python.exe -m all_sky_alert --config config.toml test-email
```

Set `[gmail] enabled=true`, then explicitly send one test:

```powershell
.venv\Scripts\python.exe -m all_sky_alert --config config.toml test-email --send
```

This standalone test message bypasses event milestones intentionally and is sent
only to the configured recipient. Restart the service to enable normal email.
Missing credentials or delivery errors never stop predictions. OAuth apps in
Testing can have short-lived refresh tokens; review Google's current
[OAuth guidance](https://developers.google.com/identity/protocols/oauth2/native-app)
and [consent/testing documentation](https://developers.google.com/workspace/guides/configure-oauth-consent).
Unverified/published apps can have verification requirements; do not assume
continuous unattended refresh while remaining in Testing.

Gmail acceptance is recorded separately from inbox receipt. Rejected sends retry
with bounded backoff (five attempts); ambiguous timeouts, server failures or
interrupted sends become `UNKNOWN` and are not automatically repeated. Inspect
event delivery history and Gmail before deliberately requeuing a job:

```powershell
.venv\Scripts\python.exe -m all_sky_alert --config config.toml retry-delivery 12 --acknowledge-possible-duplicate
```

Use the actual delivery ID from the event JSON detail. Stop the server before this
command. A delayed routine email is canceled if the updated forecast fails policy,
the event expires, it is withdrawn, or the input validity deadline passes even
without another evaluation. Seeing expiry gates only profiles that consume it.
The original milestone record remains.
Exceptional importance can bypass poor weather, but never expired scientific
prediction inputs. Each profile decides which atmospheric inputs it consumes;
weather is not a universal requirement imposed on future observation profiles.

## Testing and verification

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m compileall -q src tests scripts
.venv\Scripts\python.exe -m pip check
```

Normal tests block live HTTP/requests transports. Fixtures exercise real local
astronomy, normalized envelopes, shared cache/restart behavior, API validation,
429/backoff and stale data, independent quality/interest, lifecycle, revision
history, deduplication, crash/ambiguous delivery, safe Gmail MIME/HTTP adaptation,
read-only UI/JSON queries, escaping, host validation, DST and polar conditions.
No normal test sends email. The explicit `source-smoke` command checks Phase 3
public connectivity without production storage or notification adapters. `evaluate`
uses configured notification methods, so it is not a mail-free smoke command.
`constraints.txt` records the tested dependency versions, including optional
Gmail/dev/HRRR/QA dependencies; constraints do not install extras by themselves.
The [Phase 4 verification report](docs/phase4-verification.md) includes optional
fixture-only Edge mobile/LAN checks and the separate public HRRR decoder probe.

## Deprecated container deployment

Synology/Docker deployment is currently unmaintained. `Dockerfile`, `.dockerignore`
and prior container reports remain for historical/future use; they are not the
recommended Pi path. Follow [native Raspberry Pi deployment](docs/raspberry-pi.md).

## Next steps

Validate experimental ISS transit tracks against actual reference observations and
fresh element updates; improve uncertainty/footprints and pass identity matching
around manoeuvres before stronger timing claims. Obtain an explicitly licensed
route source if live enrichment is wanted. Add launch/reentry visibility only with
published trajectory/geographic uncertainty inputs; no schedule-only local alerts.
Continue validating Canadian seeing and aurora heuristics against observations.
Validate optics opportunities against observed halos and representative HRRR
profiles before stronger suitability claims or more levels; establish a dependable
LAN address reservation and licensed aircraft enrichment if desired.
The [source matrix](docs/source-capabilities.md) records the other seven families'
data gaps and acceptance gates. Use [HANDOFF.md](HANDOFF.md) for bounded local
Qwen/OpenClaw tasks. Core architectural changes remain with Codex/Astra.

Project licensing is deliberately left for the owner; referenced project code
was not copied.
