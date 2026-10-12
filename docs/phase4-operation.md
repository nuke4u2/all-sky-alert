# Phase 4 operation

Primary deployment is now [native Raspberry Pi OS/Trixie](raspberry-pi.md), with
the Pi LAN URL `http://allsky-pi4.local:8008/` after explicit configuration. The
Windows addresses below describe the earlier Phase 4 development environment.
Synology/Docker deployment is deprecated and unmaintained. General Pi Camera /
Timelapse operation and the systemd procedure are in the Pi guide.

## LAN access

Current Windows development configuration binds `0.0.0.0:8008`, accepts trusted
private clients and exact hosts `localhost`, `127.0.0.1`, `::1`, `192.168.1.40`
and `weissbier`. Open http://127.0.0.1:8008/ locally or
http://192.168.1.40:8008/ from the same home LAN. The hostname may also resolve as
http://weissbier:8008/; hostname resolution was not verified on another device.
The selected IP belongs to Ethernet, not the WSL virtual interface.

```powershell
Set-Location E:\AI\AgentProjects\all-sky-alert
.venv\Scripts\python.exe -m pip install -c constraints.txt -e ".[dev,hrrr]"
.venv\Scripts\python.exe -m all_sky_alert --config config.toml serve
```

Stop with Ctrl+C before restarting or running configuration maintenance. Do not
overwrite an existing config with the example. `ipconfig` shows your current IPv4
address. Reserve it in your router or update `service.allowed_hosts` when it changes.
Binding/host configuration is edited locally, not through Settings. No firewall,
router forwarding or public exposure was enabled by this development task. If a
phone cannot connect, allow the chosen Python/service port on Windows's **Private**
network profile with **LocalSubnet** scope only, and check Wi-Fi client isolation.
Do not disable the firewall. A connection from this PC through its LAN interface
does not prove another device's firewall/Wi-Fi route.

The example remains localhost-only. To opt it into a home LAN:

```toml
[service]
bind_host = "0.0.0.0"
port = 8008
lan_access = true
allowed_hosts = ["localhost", "127.0.0.1", "::1", "YOUR-PRIVATE-IP", "YOUR-HOSTNAME"]
trusted_proxy_ips = []
```

Use actual hostnames/IPs, not placeholders or `*`. Explicit IPv4 private/loopback
and IPv6 loopback/ULA/link-local clients are accepted in LAN mode; public clients
are rejected. IPv4 binding does not implicitly listen on IPv6. For IPv6 use a
supported `::` or explicit private bind address, exact unbracketed IPs in the host
allowlist and bracketed URLs. Dual-stack socket behavior depends on OS/container.
Port must be an integer from 1024 to 65535. CLI `serve --host/--port` overrides
still pass network validation; wildcard/non-loopback binding requires LAN policy.

Forwarded headers are ignored by default. A reverse proxy must use an exact trusted
peer IP in `trusted_proxy_ips`; wildcards/subnets are rejected. Keep the public-facing
host explicitly allowlisted; forwarded host is not an alternative authorization.
The service has no application authentication: every accepted trusted LAN client
can read predictions/location and change the operational settings. Do not publish
this admin surface to the public internet.

## Mobile pages, filtering and health

The existing server-rendered UI remains Jinja/HTML with small self-hosted scripts.
Navigation wraps, filters and Settings checkboxes have large label hit areas, and
tables reflow into labeled rows on phones. Map/detail/settings avoid timed full-page
reload; only the home page reloads every five minutes. Health and aircraft JSON
poll every 15 seconds. Upcoming/Now filters come from `registry.DETECTORS` labels;
All/None and multi-selection persist in the query URL. Recent records remain
unfiltered. Filtering does not change detector execution or storage.

The stoplight links to detailed source/worker health and shows evaluation time:

| State | Meaning |
| --- | --- |
| Green | Required data for enabled capabilities are sufficiently fresh |
| Yellow | Stale/degraded-but-retained data, enabled optional loss, weather/seeing fallback |
| Red | Required source invalid/unavailable, ephemeris unavailable, worker failed/overdue, central health/database read failure |
| Gray | No initial worker/source evaluation yet |

Disabled optional providers do not cause warnings. Disabling a required source
while its consumer stays enabled is red; disable that detector as well if intended.
Source-specific observation,
model-run, validity and ephemeris-coverage policies remain authoritative; no global
age threshold is imposed. A red ADS-B capability does not stop astronomy. Foreground
health polling failure shows red rather than retaining an old green badge.

## Settings and configuration evolution

`/settings` exposes a fixed allowlist: registered detector toggles, shared weather
and Canadian seeing, ADS-B/orbit/aurora/HRRR source toggles, notification/Gmail/map
toggles, common interest/quality/altitude/radius/history/optics thresholds and Gmail
event-family selection. Whole resulting configuration is schema-validated.
Unlisted values/invalid combinations are rejected. Coordinates, timezone, recipients,
credentials, paths, source URLs and raw TOML are not exposed for editing.

POST is JSON-only, capped at 32 KiB, same-origin and protected by a signed,
HttpOnly/SameSite Strict cookie/header CSRF pair. A revision hash rejects stale
forms and concurrent local edits. TOML Kit preserves comments and unrelated values;
save writes/fsyncs a same-directory temporary file, rechecks the revision, then
atomically replaces the target. File ownership/parent permissions must allow that
operation. A config-change health record records changed key names without values.
Save says **Saved — restart required**. Runtime retains the startup config; reload
Settings to inspect the disk values and restart banner. Stop/restart the service
to apply them. There is no dynamic source reconstruction or secret handling in the
browser. CSRF is not authentication.

For future schema additions, stop the service, back up your configuration privately,
then run:

```powershell
.venv\Scripts\python.exe -m all_sky_alert --config config.toml config-sync
```

It adds missing validated defaults, preserving existing values, observer fields,
Gmail paths and comments. It is idempotent and makes no provider/mail requests.
New detectors' module defaults are disabled unless explicitly chosen. It is not a
migration framework and cannot silently rename/remove unknown obsolete keys.
Use one writer and stop before CLI/manual edits; revision checking is not a general
cross-process configuration transaction system.

Gmail `event_types=["*"]` includes current **and future** registry families. An
explicit list, e.g. `["eclipses", "sky_crystals"]`, excludes future families; `[]`
selects none. Global Gmail and notifications switches still apply. Delivery uses
this selection when a **new** milestone is recorded; existing milestones are not
backfilled. Restart cancels queued disallowed mail before delivery. UNKNOWN outcomes
remain non-retrying. OAuth and preview/explicit test-email commands are unchanged.

## Aircraft map

`/aircraft` reads `/api/aircraft` every 15 seconds; `/api/map-config` contains only
public tile settings. The server owns one ADSB.lol acquisition/cache/backoff path.
The transient buffer has independently bounded time, aircraft count and point
count, holding at most 12,000 points at defaults (54,000 at allowed maxima). Reads
are locked, bounded and I/O-free; there is no position row written for every map
sample. Position expiry and PIA/LADD/anonymization gates apply. Restart loses trails,
but persistent interesting-aircraft episodes and milestone identities survive.

Routine craft can be displayed without an event. White heading ticks describe
ground track; cyan solid paths are observed breadcrumbs. Gold dashed paths are
short constant-motion projections (at most 180 seconds), not guaranteed or filed
routes. Track turns, climbs and visual sightings remain unresolved. Selection shows
callsign/type, altitude source, direction, position age, range/elevation and separately
aged licensed enrichment; missing route data stay unknown.

Leaflet 1.9.4 is vendored under BSD-2-Clause with the license retained. OpenStreetMap
tiles are optional, attributed, browser-cached and requested only for human map
viewports; no prefetch/bulk/offline downloads. The map page allows the configured
HTTPS tile origin for images and sends an origin Referer. Browser QA disables tiles.
Respect the [OSM tile policy](https://operations.osmfoundation.org/policies/tiles/).
Default tiles are best-effort; change to an appropriately licensed provider as
usage grows. A custom `tile_url` must be credential-free HTTPS with `{z}/{x}/{y}`
in the path and no query/fragment; set its `tile_attribution` plain-text credit.
Never place a provider secret in this browser-visible configuration.

| New map option | Default / bounds |
| --- | --- |
| enabled / tiles_enabled | true / true; map also requires ADS-B detector |
| tile_url | `https://tile.openstreetmap.org/{z}/{x}/{y}.png` |
| tile_attribution | `OpenStreetMap contributors`; 1–256 plain-text characters |
| radius_km | 40; 1–185, an interest overlay independent of feed radius |
| track_minutes | 10; 1–30 |
| max_aircraft / max_points | 150 / 80; 1–300 / 2–180 |

A smaller upstream `providers.adsb.radius_nm` means missing data outside that feed
radius even if the overlay is larger. Payload and per-browser rendering are bounded;
several viewers add local JSON reads, not extra upstream calls. Browsers clear
expired markers and clear current craft on failed refreshes.

## Atmospheric optics and sparse HRRR

`sky_crystals` injects the same shared conditions service as other visual families.
Surface temperature and dewpoint join the existing combined Open-Meteo request.
Cold high-cloud evidence, low-cloud obstruction and airless Sun/Moon geometry
produce **uncalibrated opportunity** scores rounded to five points. High clouds do
not prove ice habit/orientation or optical depth. Circumzenithal arcs require Sun
elevation below 32.2°; distinct tangent arcs use conservative low-Sun screens;
above their transition they can become circumscribed forms that this slice does
not model. Pillars use low Sun, lunar halos require Moon above 5° and at least half
illuminated. Geometric closing boundaries are clipped; forecast suitability is
still hourly and point-based. Window identity is phenomenon/body/local day;
reevaluation updates the best contiguous sampled window for that day.

Diamond dust uses a separate near-surface threshold: temperature ≤ −20°C, wind
≤3 m/s and approximate humidity over ice ≥95%. The liquid-water RH-to-ice conversion
uses a Magnus saturation approximation. Inversions, ice fog, crystal observations
and light-source-specific pillars remain unknown; no high-cirrus model is reused.
Coarse confidence .25 normally does not meet the default .3 alert threshold;
inspect opportunities without expecting every one to email. Intrinsic interest
stays 60/65 independently of weather quality. Never look directly at the Sun or
use unfiltered optics to examine these phenomena; shield the Sun from view.

| New detector option | Default / bounds |
| --- | --- |
| enabled | false in module defaults; explicitly true in both supplied configs |
| interval_seconds / horizon_hours | 1800 / 48; 300–86400 / 1–72 |
| minimum_opportunity | 35/100; 0–100 |
| phenomena | `halo22`, `parhelia`, `cza`, `upper_tangent`, `lower_tangent`, `pillar`, `lunar_halo`, `diamond_dust` |
| diamond_temperature_c | −20; −50 to −10 |

Optional `providers.hrrr` is a shared domain-data provider consumed by crystal
detection; there is no GRIB acquisition inside the detector. Coverage is conservatively
CONUS (24–50°N, 125–66°W) and nearest point must be within 20 km. Install `.[hrrr]`
for ecCodes. Missing decoder, unsupported location, rate limits, index/parameter
failure or outage yields coarse fallback and yellow health when enabled.

NOAA's public AWS natural-level GRIB indexes locate selected CIMIXR, NCCICE, CLMR,
TMP, SPFH, PRES and HGT records. Only exact bounded byte ranges with HTTP 206 and
matching Content-Range are accepted; HTTP 200 full files are refused. Numeric GRIB2
parameter identity/center/level/run/forecast and published units are checked, then
nearest-grid data extracted. HRRR's old table-version metadata can make ecCodes
label newer ice parameters unknown; official NCEP parameter numbers/units resolve
that mapping explicitly. **NCCICE is number per kg, not number per cubic metre.**
RH is derived from specific humidity, pressure and temperature; a natural-level RH
record was not available. Model issue, sample valid, retrieval and expiry times are
separate. Warm/liquid-only/missing sampled ice is weak evidence, not a full-profile
absence claim. Number concentration and cloud water are retained as evidence, not
converted to crystal orientation. Fractional cloud cover, vertical velocity,
full-profile integration, directional obstruction and calibrated optical properties
are deliberately deferred.

| HRRR option | Default / bounds |
| --- | --- |
| enabled | false in module/example; true in current Windows development config |
| cache_seconds | 3600; 3600–21600 |
| max_run_age_hours | 8; 6–12 |
| levels | `[20,30]`; 1–3 unique hybrid levels, each 1–50 |
| forecast_hours | `[3,6]`; 1–3 unique steps, each 0–18 |
| max_download_mb | 32; 4–64, entire selected run budget |

Choose six-hourly initialization after a two-hour publication lag. Immutable extracted
samples are persisted by run/config/observer and reused across hourly checks and
restart. Default acquisition is two indexes plus 28 records per new six-hour run,
max 8 MB/record and 32 MB total, at most four runs/day. Failures back off at least
one hour; there is no run-search request burst. A full +6h probe transferred
12.5 MB; both default steps are approximately 25 MB/run, ~100 MB/day. Only targets
within one hour of a selected valid time use those samples; other hours use shared
coarse weather. Old extracted runs are pruned after seven days.

The development HRRR source was enabled only after successful public/credential-free
Windows decoding and bounded per-run cache verification. The portable example
leaves it opt-in to avoid continuous GRIB bandwidth/dependency surprises.

Sources: [NOAA public HRRR](https://registry.opendata.aws/noaa-hrrr-pds/),
[natural-level inventory](https://www.nco.ncep.noaa.gov/pmb/products/hrrr/hrrr.t00z.wrfnatf00.grib2.shtml),
[moisture parameter table](https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/grib2_table4-2-0-1.shtml),
[cloud parameter table](https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/grib2_table4-2-0-6.shtml),
[ecCodes Python](https://github.com/ecmwf/eccodes-python),
[optical altitude reference](https://www.atoptics.co.uk/blog/cza-effect-of-solar-altitude/).

## Security review

Exact Host validation handles ports/bracketed IPv6 and rejects malformed/duplicate
headers. LAN peer checks and trusted-forwarded-peer configuration are distinct.
All provider strings are HTML-escaped or rendered through `textContent`; custom
tile credits are escaped before Leaflet consumes HTML. Source links reject unsafe
schemes. No generic file editor, shell endpoint, path traversal or credential JSON
route was added. Unsupported methods receive framework 405. CSP blocks external
scripts, eval, objects, framing and foreign form submission; map-only inline styles
are allowed for Leaflet positioning. Configuration POSTs require exact same origin.

Native deployment requires a writable configuration parent directory for atomic
replacement, local SQLite storage and protected OAuth files. Use one coordinator,
without workers/reload scaling. See [Pi deployment](raspberry-pi.md) for paths,
systemd/device permissions and ARM64 acceptance. Synology/container deployment is
deprecated; historical verification remains in the prior reports. Keep actual
config, secrets and runtime data out of source transfers and handoffs.
