# Phase 3 implementation and verification

Historical Windows/container evidence; these commands are not the current Pi
installation path. See [README](../README.md) and [re-audit](pi-reaudit.md).

Completed against the Phase 2 workspace on 2026-10-09. Read the
[design recorded before implementation](phase3-architecture.md),
[source decisions and authoritative references](source-capabilities.md), and
[run/configuration instructions](../README.md).

## Claims delivered

| Feature | Status and claim |
|---|---|
| ISS visible passes | Implemented: elevation, observer darkness and satellite illumination throughout eligible segments; geometric rise/maximum/set, range, azimuths, epoch and separate cloud/transparency assessment. An opportunity, not a confirmed sighting or validated magnitude. |
| ADS-B aircraft episodes | Implemented: fresh observer-relative positions, bounded approaching estimates, sky-motion prose, configured interest rules, persistent episode/revision/milestone identity, hysteresis and gap continuity. No confirmed visual detection, inferred airport pair or long-range flight path. |
| Shared orbital service | Implemented: validated modern GP/OMM, replaceable source contract, SGP4, WGS84 observer and offline DE421 asset. Configured catalog IDs can generate sunlit geometric passes; non-ISS brightness/detectability remains unknown. |
| ISS Sun/Moon transits | **Experimental** nominal center-track disk contacts, refined below one second and independently checked on frozen orbital cases. Solver precision is not physical timing accuracy. Solar safety is shared with eclipses in detail pages and email. Moon geometry has no darkness gate. |
| Aircraft enrichment | Implemented optional user-licensed local records with independent age/expiry, route binding and provenance. Failures do not gate tracks. No third-party route database or commercial subscription incorporated. |
| Live UI and milestones | Implemented 15s focused aircraft JSON polling, client-side expiry/clearing, independent detector/provider cadence and clock-only scheduled milestones in the delivery loop. |
| Launches, plumes, venting, fuel dumps, reentries | Deferred: researched schedule/first-GP/DECAYING/TIP inputs do not establish reliable local visibility. No unused source adapters or invented predictions. |

Existing planets, sunset, Canadian seeing, eclipses, aurora, Gmail safety and history
remain covered by the regression suite. Ice optics, Galilean moon/shadow/GRS events,
occultations and comets remain outside this phase. Space-Track authentication,
automatic catalog discovery and remote commercial enrichment are also deferred.

## Components and contracts

- `orbit_data.py`: normalized OrbitRecord/OrbitSource, CelesTrak adapter, persistent
  shared cache, epoch health, global HTTP stop and shared geometry construction.
- `orbits.py`: local SGP4/topocentric geometry, Earth shadow, apparent body vectors,
  rise/max/set, angular motion, minimum/contact refinement and asset provenance.
- `satellites.py`: visible-pass and nominal ISS transit consumers. No detector HTTP
  or delivery; both consume common atmospheric assessment profiles.
- `adsb.py` / `aircraft.py`: timestamped source states versus local geometry,
  bounded motion, interest and evolving track episodes. Position validity is
  independent of episode continuity. Filter PIA/LADD/anonymized records.
- `enrichment.py`: optional, separately aged user-supplied metadata; exact
  aircraft/callsign route binding, explicit rights/source/expiry, no route inference.
- Composition/assessment/configuration register the concrete consumers; sources
  are instantiated once. Optional dependency failures are non-gating. Schema 3
  additively creates `track_episodes`; existing prediction/revision/outbox keys stay.
- Delivery reevaluates persisted scheduled policy every 15s; optimistic comparison
  against `evaluated_at` protects concurrent detector updates. UI routes remain
  read-only. External JavaScript uses `textContent`, bounded requests and expiry.
- Example configuration, architecture/research/source documentation, developer
  contracts and bounded handoff guidance were updated. `config.toml` was untouched.

No referenced project implementation was copied. Installed Skyfield/sgp4 dependencies
are MIT; numerical Vallado reference vectors include attribution/license in
[fixture notes](../tests/fixtures/README.md). Project licensing remains the owner's
decision. No commit or git initialization occurred; the directory is not a checkout.

## Gates and source choices

| Source | Defaults / operational contract |
|---|---|
| CelesTrak public per-catalog GP JSON | No credentials; at least 7200s persistent cache shared by both orbital consumers. **Any non-200 persists a global HTTP stop** until explicit operator reset. Transport/validation retry also waits at least the GP cadence; cached evaluations never slide that retry deadline. Minimal queries, no automatic fallback or stop reset. |
| Orbital epoch | Warning after 24h, stale after 48h, invalid after 96h. Target propagation age recorded; scientific expiry prevents routine stale-input alerts. Transits require age <=12h now and <=24h at target. Fresh GP can still be wrong after manoeuvres; no covariance supplied. |
| ADSB.lol observer radius | Selected public source with documented ODbL 1.0 terms and attribution. Dynamic quotas, not a promised numeric entitlement; one 60nm request every 15s by default, persistent Retry-After/backoff. Position time is feed millisecond `now` minus second `seen_pos`; stale after 30s, invalid after 90s. No new candidate/current UI claim after its individual 30s validity. |
| Enrichment | Disabled by default; 3600s acquisition cache, 24h maximum metadata age, explicit validity <=7 days. Type may be aircraft-bound; route must also match callsign and both endpoints. No stale/unknown route inference. |
| Local assets | Skyfield 1.55, sgp4 2.27, skyfield-data 7.0.0, JPL DE421; conservative 1900–2053 coverage. Built-in time tables, no service-time kernel/EOP download. Kernel SHA-256 `a20a7139da04cbc462454634918e9a9ca69127044e2cc9d4f9c16e238d2deedc`. |

OpenSky was evaluated but its anonymous 400 daily credits cannot sustain 15s
polling. Airplanes.live data permissions/access were not sufficiently verified.
ADSBDB restricts route incorporation; public reachability does not establish rights.
Review ODbL obligations before publishing a derived database. Space-Track account
terms/credentials and paid services were not used. Launch Library schedules provide
no local trajectory; TIP descent coordinates are not a local glowing-reentry or
impact forecast. The source matrix links the current primary documentation.

## Automated results

Baseline before product changes: **137 passed**. Final complete suite:
**180 passed, 7 dependency deprecation warnings, 16.04s** on Windows/Python 3.12.
The 43 added cases include parameterized cases; this count is not a count of test
functions. All normal tests prohibit uncontrolled HTTP and OAuth refresh requests.
No actual email was sent.

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m compileall -q src tests scripts
.venv\Scripts\python.exe -m pip check
```

Final pytest execution used a private `test-artifacts/` basetemp and disabled its
cache provider because this environment restricted pytest's default temp writes.
Compileall passed; pip check reported no broken requirements. Warnings are the
existing Starlette TestClient/httpx deprecation and Skyfield's NumPy 2.5 dtype
deprecation, not failed assertions. No extra lint/type-check tool is configured.
`node --check src/all_sky_alert/static/live.js` also passed with the available
Node 24 installation; Node is not an application dependency. All local Markdown
documentation links resolved.

Coverage includes:

- Frozen SGP4 vectors, independent observer geometry/shadow, rise/max/set and
  refined solar/lunar contacts, daylight Moon transits, grazing/miss cases and
  scientific epoch expiry (including exceptional-interest bypass).
- Shared orbit cache/restart, source identity invalidation, global non-200 stop,
  fixed transport retry deadline, stale/future epochs and invalid payloads.
- Aircraft normalization/privacy, azimuth/elevation/range, unknown motion, aged
  state projection, bounded closest approach and observer-relative direction.
- Episode identity/update/absence/hysteresis/restart, expired positions, source
  429/outage, independent health aging and HTTP 200 with old scientific data.
- Enrichment success, expiry, unknown/rights/route binding, missing-file and
  unexpected source/snapshot failures without suppressing aircraft detection.
- Serial lane/detector cadence isolation, scheduled milestone timing without
  geometry, concurrent-update protection, migration from schema 1/2, old-config
  defaults, revisions, alert deduplication/restart and no per-poll mail storm.
- HTML escaping, JSON fields, transit safety in rendered email without sending,
  live expiry, source health, DST and all Phase 2 regression behavior.

## Independent numerical validation

Tolerances were recorded in the design before implementation:

| Calculation | Independent reference and accepted tolerance | Result |
|---|---|---|
| SGP4 TEME position/velocity | Published Vallado case 5 vectors at 0/360/720 minutes; position 1e-5 km, velocity 1e-8 km/s | All pass. Frozen OMM transcription exercises the actual chosen propagator. |
| Observer geometry | Direct SGP4 TEME, UTC GMST and independent WGS84/ECEF/ENU transformation; 0.02° / 1km | All samples pass at Greenwich, Sydney and a high-latitude/date-line observer. Frame/UT1 approximations are disclosed. |
| Illumination and rise | Independent TEME/Sun cylinder geometry away from penumbra; independent altitude root | Four illumination samples match; rise differs <0.15s. Skyfield is the production shadow/pass backend. |
| Physical solar/lunar transit fixtures | Direct SGP4 plus Astronomy Engine 2.1.19 apparent body directions, independent 0.01s scan/bisection; contacts <=0.1s, separation <=0.02° | Both cases pass; measured contact differences <0.003s. Solar nominal duration 1.135904s; lunar 0.874239s. Both occur at daylight observers. |
| Synthetic between-sample/grazing/miss search | Independent analytic disk crossings; contacts <=0.002s | All pass, including a crossing shorter than one second between 5s samples. Tangency has no positive center-track duration. |

The physical fixtures use a frozen public ISS OMM from 2024-05-06 and observer
locations selected independently on its nominal centerline. **They are not observed
sightings or a third-party transit-site prediction.** `tests/orbit_reference.py`
does not import product orbit geometry/solvers. These checks validate the algorithm
on fixed inputs; they do not calibrate real-world prediction error. Solar/lunar
contacts exclude spacecraft silhouette/attitude, terrain and limb topography.

## Operational checks

Explicit `source-smoke` ran against public CelesTrak and ADSB.lol with a separate
private database. GP fetched successfully at 2026-10-09 04:26 UTC with element epoch
2026-10-08 20:29 UTC. A second check at 04:41 UTC reused it, confirming no duplicate
GP download inside two hours. ADSB.lol returned fresh positions. These are one-time
connectivity observations, not availability guarantees.

A full localhost run at 04:42 UTC used the example Greenwich observer, separate
SQLite, no Gmail adapter and notifications disabled. It produced 26 planetary,
8 sunset, 3 eclipse and 1 aircraft predictions. Root, prediction/live/health JSON,
the JavaScript asset and all 38 detail pages returned 200 on actual Uvicorn TCP.
All consumed external sources were healthy; enrichment was disabled. First
evaluation took 6.149s. Weather/seeing/OVATION each retained their existing cadence;
the previously fetched GP was seeded into the private cache to avoid another request.

No visible ISS/transit/aurora event was fabricated to fill the page. The next 48h
contained 12 geometric ISS passes at this observer, all failing darkness/elevation
requirements for the configured visible subset. The orbital fixture tests and
offline container check demonstrate positive predictions at eligible times.

Docker built successfully as `all-sky-alert:phase3` on Linux amd64. An actual
**network-disabled** container ran as UID 10001 with only public/synthetic fixtures
mounted read-only, temporary SQLite and no credentials/mail. It produced 5 eligible
ISS passes and 2 aircraft episodes; 12 actual HTTP routes returned 200. Reconstructed
providers/detectors retained event IDs and alert counts across restart; packaged
kernel identity matched. No runtime asset download was possible. Reproduce:

```powershell
docker build -t all-sky-alert:phase3 .
$fixturePath = (Resolve-Path tests/fixtures).Path
Get-Content -Raw scripts/phase3_container_smoke.py | docker run --rm -i --network none --mount "type=bind,source=$fixturePath,target=/fixtures,readonly" --entrypoint python all-sky-alert:phase3 - /fixtures
```

Synology hardware/ARM builds and visual browser automation were not exercised.
HTTP/template and JavaScript-serving checks do not substitute for a NAS soak test
or observed transit validation. No production database/configuration, Gmail secret
or token, unrelated project directory or existing mail setup was accessed.

## Manual setup and next bounded work

Upgrade the environment with the README's constrained pip command. Preserve your
existing observer/configuration; omitted Phase 3 detectors stay disabled. Add the
desired ISS/transit/ADS-B tables and per-kind policy overrides from the example,
then launch `python -m all_sky_alert --config config.toml serve` from the venv and
open **http://127.0.0.1:8008/**. CelesTrak/ADSB.lol need no credentials. Gmail remains
optional and uses the unchanged OAuth flow; no additional Gmail setup is required
for these event kinds. Enrichment requires your own explicitly licensed input.

The architectural review found no universal darkness/weather requirement blocking
future detectors. Shared orbit geometry now supports precision consumers; transient
episodes and optional dependencies are concrete extension points. Asteroid/comet
inputs, occultation footprints, Jovian drift, optical cloud properties, launch
trajectories and geographic reentry uncertainty still require domain-specific
providers/profiles. They can reuse envelopes, provenance, persistence and delivery
without changes to existing detector modules. Avoid using GP pass geometry as a
launch/reentry/plume visibility model.

Recommended next step: independently compare experimental transit tracks against
fresh authoritative/observed cases across locations and manoeuvres, then add
uncertainty footprints and stronger occurrence matching. Retain the experimental
label until that evidence supports a stronger claim. Continuous live-source/NAS
soak testing and an explicitly licensed route source are separate bounded tasks.
