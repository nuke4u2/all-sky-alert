# Phase 4 verification — 2026-10-10

Baseline inspection found seven real detector families, shared Open-Meteo/ECCC,
SQLite revisions/outbox/aircraft episodes, separate worker lanes, and Gmail OAuth.
There was no map, editable Settings, aggregate stoplight, ice-optics detector or
HRRR provider. Actual config already enabled Phase 3 and kept Gmail disabled.
Baseline: **180 passed, seven warnings, 16.62 seconds**. No git repository was
present, so a git diff is unavailable; no commit/push/init was performed.

## Changes and architecture review

Kept the modular monolith and event/storage/outbox contracts. New concrete files:

- `network.py`: validated bind/private peers/exact hosts/trusted proxy policy.
- `config_edit.py`: fixed operational controls, full validation, revision guard,
  comment-preserving atomic save and `config-sync` support.
- `health.py`: enabled-capability aggregation over SourceHealth contracts.
- `tracks.py`: bounded in-memory observed tracks and normalized map snapshots.
- `crystals.py`: real shared-atmosphere/local-geometry opportunities.
- `hrrr.py`: optional selective natural-level acquisition/decode/run cache.
- Settings/aircraft templates, `ui.js`, `settings.js`, `map.js`, responsive CSS,
  self-hosted Leaflet 1.9.4 assets and retained BSD-2-Clause notice.
- Three offline test files, one frozen NOAA GRIB record, fixture browser/container
  smoke scripts, Phase 4 design/operation/verification documentation.

Existing config/registry/source composition/service/assessment/web/CLI/provider
modules gained only the needed consumers and contracts. Registrations supply
filter/enable/email choices, source dependencies/health prefixes and worker lanes.
Gmail receives no detector-specific branching; allowed adapters are chosen at new
milestones and disallowed pending jobs are canceled without historical replay.
Shared conditions gained surface temperature/dewpoint in the same acquisition.
SQLite schema remains 3; map points do not become prediction rows. Existing event
revisions, scientific expiry, alert deduplication and restart identity persist.

Self-review fixes include weather/seeing initialization degradation, disabled
required-feed red status, recoverable atmosphere/delivery heartbeat status, safe
CSRF comparison, coherent single-read config revisions, configurable escaped tile
credits and geometric window closing limits. Required feed loss does not prevent
unrelated detectors from calculating; disabled optional sources do not degrade.
Adding the next detector exposes filter/enable/email choices through its one
registration. Adding a provider uses SourceHealth and one source registration.
HRRR is a prepared shared source, not detector-owned weather I/O. Config evolution
uses validated explicit sync, not a new migration/hot-reload framework.

## Actual and example configuration changes

Both files were schema-validated and compared against private pre-phase backups.
All original observer coordinates/timezone, Gmail values/paths and other supplied
values are preserved. **25 new keys each**; the only changed pre-existing key is
actual `service.allowed_hosts`, appending `192.168.1.40` and `weissbier`.

| Added entries | Actual development values | Example difference |
| --- | --- | --- |
| service.bind_host, port, lan_access, trusted_proxy_ips | `"0.0.0.0"`, `8008`, `true`, `[]` | `"127.0.0.1"`, LAN false |
| gmail.event_types | `["*"]`; existing Gmail remains disabled | Same; all current/future families |
| detectors.sky_crystals.enabled, interval_seconds, horizon_hours, minimum_opportunity, phenomena, diamond_temperature_c | `true`, `1800`, `48`, `35`, all eight implemented phenomenon IDs, `-20` | Same |
| providers.hrrr.enabled, cache_seconds, max_run_age_hours, levels, forecast_hours, max_download_mb | `true`, `3600`, `8`, `[20,30]`, `[3,6]`, `32` | HRRR false, opt-in optional decoder/bandwidth |
| map.enabled, tiles_enabled, tile_url, tile_attribution, radius_km, track_minutes, max_aircraft, max_points | `true`, `true`, OSM HTTPS XYZ template, `"OpenStreetMap contributors"`, `40`, `10`, `150`, `80` | Same |

The actual new credential-free detector/map/LAN features are enabled. HRRR was
enabled after its public Windows decode probe and immutable run-cache checks;
source queries are bounded at approximately 25 MB per six-hour run with defaults.
No secrets/tokens were copied into either file. Comments in the example explain
LAN/proxy trust, opt-in microphysics, email selection and tiles/trail privacy.
Config-sync idempotence and preservation are covered offline; Settings saves to
the actual configured file, without runtime reload, and audit only changed keys.

## Automated and browser checks

**229 tests passed in 18.31 seconds**, including **49 new parameterized cases** across:

- `test_lan_settings.py` (20): IPv4/IPv6/Host/public-client handling, protected
  settings rendering, CSRF/body/validation failures, atomic replacement failure,
  edit conflict, detector/email saves and value/comment preservation during sync.
- `test_health_routing_map.py` (8): required/optional/disabled/fallback/worker/DB
  health, provider-specific ages, no mail replay, pending cancellation, read-only
  routine aircraft map, escaping/expiry, track bounds and multi-select query filters.
- `test_crystals_hrrr.py` (21): constrained geometry, boundary clipping, obstruction,
  separate diamond-dust inputs, shared conditions, stable updated identity,
  optional failure fallback, units/value validation, indexed ranges, immutable
  restart cache, GRIB time/parameter units, expiry, 200/429/503 and backoff.

Seven pre-existing dependency warnings remain: Starlette TestClient/httpx
deprecation and Skyfield's NumPy dtype deprecation. No network or mail occurs in
normal pytest. Compileall, pip check and all four application JS syntax checks pass.

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m compileall -q src tests scripts
.venv\Scripts\python.exe -m pip check
```

Optional browser QA uses installed Edge 155, private temporary TOML/SQLite, mocked
feeds, real local astronomy and frozen reference cases. For rendering-only branches
at other sites/dates, fixtures are clearly isolated from operational predictions.
All upstream browser requests and external tiles are blocked; Gmail adapters absent.

```powershell
.venv\Scripts\python.exe -m pip install -c constraints.txt -e ".[qa]"
.venv\Scripts\python.exe scripts/phase4_browser_smoke.py --lan-host 192.168.1.40
```

**55 successful page checks** at widths **360/390/430/768/1280**, covering `/`,
`/settings`, `/aircraft` and all eight detector detail branches. No page-wide
horizontal overflow or browser script errors. Checked source-health table reflow,
All/None/multi-select URL persistence, hostile callsign rendered literally, map
selection/zoom/centering/projection toggle, atomic Settings save and explicit
restart banner; running settings remained unchanged. Mobile screenshots were
visually inspected. Private artifacts: `test-artifacts/phase4-browser/`.

Both localhost and Ethernet `192.168.1.40` HTTP returned 200 on isolated port 8021;
unapproved Host returned 400. Development config uses the equivalent binding/policy
on port 8008: http://127.0.0.1:8008/ and http://192.168.1.40:8008/.
**No separate phone/tablet connection or hostname-resolution test was performed.**
Firewall/router settings were not changed; Private/LocalSubnet ingress and Wi-Fi
isolation may need user configuration. Production storage was not opened.

## Public HRRR probe and Linux checks

Separate credential-free Windows probe: HRRR initialization **2026-10-10 12Z**,
valid **18Z**, retrieval **14:44:44Z**. One +6h forecast, two levels, seven fields
each, 14 exact ranges plus index, **12,498,368 bytes**. ecCodes 2.49 decoded nearest
point ~1.39 km from the observer. Sampled heights ~6.65/11.54 km, temperatures
256.82/221.51 K and cloud ice **zero** at both levels: successful ingestion does
not imply a favorable halo forecast. No full GRIB file was downloaded and no
continuous production provider or mail was run. Private report:
`test-artifacts/phase4-hrrr/source-report.json`.

Linux x86_64 image built with `EXTRAS=gmail,hrrr`. Under `--network none`, UID 10001:

- Existing Phase 3 smoke: five ISS passes/two aircraft, 12 HTTP routes,
  restart identity and deduplication pass.
- Phase 4 smoke: frozen HRRR numeric identity/units decode, real optics windows,
  routine map data/packaged assets, 27 HTTP routes and atomic settings save/restart
  banner pass. No Gmail/credentials/upstream traffic.

```powershell
docker build --build-arg EXTRAS=gmail,hrrr -t all-sky-alert:phase4 .
docker run --rm --network none `
  --mount type=bind,source=E:\AI\AgentProjects\all-sky-alert\scripts,target=/smoke,readonly `
  --mount type=bind,source=E:\AI\AgentProjects\all-sky-alert\tests\fixtures,target=/fixtures,readonly `
  all-sky-alert:phase4 python /smoke/phase4_container_smoke.py /fixtures
```

No Synology hardware or ARM image was tested. Directory-mount configuration,
explicit binds/private hosts/trusted proxies and `/data` persistence use the same
portable code. QA/browser dependencies remain development extras, not runtime.

## Remaining limits and next phase

No application authentication; trusted home LAN only. Settings requires restart,
one writer and directory replace permission. DHCP/hostname/firewall/device routing
remain deployment-specific. Tiles disclose viewed region, are optional and best-effort.
Trails are transient; projections cannot predict turns/climbs or filed routes.
Licensed enrichment remains optional; no new live route source was invented.
HRRR layers/times are sparse, not a full profile. Optical thresholds and confidence
are uncalibrated; crystal habit/orientation, directional clouds, inversion/ice fog,
additional light-source pillars and actual optical depth remain unknown. Coarse
opportunities may not meet default alert confidence. Existing ISS transit timing
uncertainty remains unchanged.

Recommended next phase: collect observed optics outcomes and representative
multi-run HRRR fixtures to validate/calibrate layer suitability, then improve
vertical/line-of-sight evidence only where justified. Separately validate the
phone connection and NAS proxy/volume permissions; add app authentication only
if access needs extend beyond the trusted LAN.
