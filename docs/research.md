# Research notes — 2026-10-06

Original decisions are preserved below. Current Phase 3 research (2026-10-09) is
in [source-capabilities.md](source-capabilities.md); see also
[phase3-architecture.md](phase3-architecture.md).

Phase 2 research (2026-10-08) is in [source-capabilities.md](source-capabilities.md):
all nine requested detector families, current public operational checks, limits,
licences/data gaps and shared-provider decisions. The [extension design](phase2-architecture.md)
and [verification report](phase2-verification.md) distinguish implemented eclipse/
aurora functionality from deferred orbit, aircraft, optics and small-body work.

Research preceded the design. One read-only research subagent reviewed the
requested repositories and source APIs; the main agent independently checked
Astronomy Engine, Open-Meteo, NASA physical constants and Google OAuth docs.
No implementation code was copied from the reference repositories.

## Comparable projects

| Reference | Useful ideas | Constraints we do not inherit |
| --- | --- | --- |
| [Overhead](https://github.com/JamesDavid/Overhead), [SPEC.md](https://github.com/JamesDavid/Overhead/blob/main/SPEC.md) | provider-owned fetch/parse/cache/freshness, scheduler, shared astronomy, event distribution, director attention policy | ESP32 memory/network/display constraints; ephemeral attention is insufficient for persistent alert milestones |
| [SkyGraph](https://github.com/ruvnet/skygraph) | separation of feed ingestion, pure behavior detection, geometry and explained scoring; degraded operation | browser persistence, graph/vector infrastructure, embeddings and cross-runtime parity do not serve this small local service |

[Overhead's license](https://github.com/JamesDavid/Overhead/blob/main/LICENSE) is
PolyForm Noncommercial 1.0.0. Copying would add use/notice obligations; we only
adopt general architectural ideas and implement independently. SkyGraph's README
declares MIT; its standalone license endpoint could not be retrieved during the
survey. That is not relied on as permission to copy; no code was copied.

## Astronomy libraries and MVP choice

[Astronomy Engine Python documentation](https://github.com/cosinekitty/astronomy/blob/master/source/python/README.md)
documents topocentric equatorial/horizontal positions and rise/set searches. Its
[MIT license](https://github.com/cosinekitty/astronomy/blob/master/LICENSE) permits
using the installed dependency; its own distribution retains the notice. Version
2.1.19 was tested. The small pure Python engine requires no kernel download, useful
for a first local planetary/sunset slice. UTC `Z` format is translated in the
adapter; the rest of the application uses aware standard ISO timestamps.
[NASA fact sheets](https://nssdc.gsfc.nasa.gov/planetary/factsheet/) supply the
equatorial diameters used for disk angular size. The ideal 550 nm Rayleigh
reference is 138.4/aperture_mm arcsec, not a guarantee of real telescope performance.

[Skyfield installation](https://rhodesmill.org/skyfield/installation.html),
[almanac](https://rhodesmill.org/skyfield/almanac.html),
[planets](https://rhodesmill.org/skyfield/planets.html) and
[satellites](https://rhodesmill.org/skyfield/earth-satellites.html) make it a strong
future domain-provider option, especially for orbital passes. It brings NumPy and
ephemeris/orbit assets that are unnecessary for this narrow pair; it can coexist
behind a satellite adapter later. DE421/DE440s should be cached immutable assets.
Orbital epoch is distinct from HTTP retrieval freshness, and pass search must
handle partial passes and multiple culminations.

[CelesTrak GP formats](https://www.celestrak.org/NORAD/documentation/gp-data-formats.php)
now default to CSV; request explicit JSON OMM and use Skyfield's from_omm when
adding ISS. Legacy TLE cannot represent newer six-digit catalog numbers. Respect
[usage policy](https://www.celestrak.org/usage-policy.php) with shared cache and
backoff. ISS requires stable revised-pass matching; a changing predicted peak
timestamp must not itself become an alert identity.

Planetary/sunset is selected because it demonstrates two contrasting assessment
profiles on the same forecast with no orbital ingestion bootstrap. Planetary
sampling searches darkness and altitude, not only meridian transit, which might
happen during daylight. Sunset time is real geometry; color quality is an
explicitly experimental heuristic. Aurora is deferred until an oval/line-of-sight
geometry and darkness assessment can replace a simplistic Kp threshold.

## Atmospheric source

[Open-Meteo API](https://open-meteo.com/en/docs) supports UTC hourly timestamps,
explicit wind units, cloud layers, humidity, visibility and pressure-level winds.
The one combined request uses `past_days=2` for currently active overnight windows
and configured future coverage. Null metrics are kept unknown; shapes, units,
ranges and time order are validated. Local horizon clouds and physical seeing
are not provided by this API; wind does not establish seeing arcseconds.

[Terms](https://open-meteo.com/en/terms) distinguish free noncommercial access from
commercial service, with CC BY 4.0 weather attribution and request limits. The UI
and email include attribution. This service normally requests once per hour, not
once per detector. Model selection, coverage and update times vary geographically.
Retrieval time is stored, but not falsely represented as upstream model issue time.
[Air quality API](https://open-meteo.com/en/docs/air-quality-api) can supply AOD
later; it is not called until a profile actually consumes it. HRRR/GOES adapters
are deferred, with no empty placeholder modules.

## Canadian seeing follow-up (2026-10-07)

Direct ECCC RDPS seeing is now implemented through the official MSC GeoMet JSON
point interface. [Source/method, licence and acquisition research](canadian-seeing.md)
records the native categories, coordinate checks, run/valid times and platform
decision. No Clear Dark Sky code/chart content is copied. This adds modeled
astronomical seeing categories while preserving the distinction from physical
arcsecond measurements and the older wind proxy.

## Gmail and service stack

[Google desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app),
[Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes) and
[sending guide](https://developers.google.com/workspace/gmail/api/guides/sending)
support loopback consent, gmail.send-only permissions and base64url MIME sends.
We implement that standard flow rather than accessing the user's other project.
Libraries are optional extras. The Gmail API does not expose an idempotency key
that gives us exactly-once delivery after a send/commit crash; uncertain outcomes
are surfaced for explicit reconciliation instead of blindly retried.

Python is chosen for astronomy libraries and modest service requirements. Go or
Rust would simplify binary distribution but would move numerical astronomy to a
less familiar ecosystem. FastAPI and Jinja keep HTTP/template work conventional;
Flask would also fit, but a second framework offers no benefit. SQLite stdlib
handles a single writer and volume persistence without SQLAlchemy migrations or
server administration. A serial interval task is sufficient; APScheduler would
add job persistence semantics we do not yet need. One OS lock enforces ownership.

## Phase 3 implementation decision

The original Skyfield deferral now has real consumers: ISS passes and refined
Sun/Moon transits. MIT Skyfield/sgp4 dependencies supply propagation/transforms;
packaged DE421 avoids runtime asset downloads. Existing Astronomy Engine still
serves Phase 2 and independently validates transit body directions. No comparable
project code is copied. A few Vallado numerical verification vectors are transcribed
with attribution and the sgp4 distribution's MIT notice to validate propagation;
no implementation is copied.

CelesTrak's policy changes generic retry behavior: persist a stop on non-200, never
silently reset at restart. Minimal catalog queries cache two hours. Epoch and
tighter transit gates remain separate. ADSB.lol radius queries/documented ODbL terms
fit tracking; OpenSky anonymous credits do not support 15s continuous polling.
Airplanes.live access/data permissions remain unresolved. readsb v2 now is
milliseconds, unlike ordinary aircraft.json. Actual position age controls validity;
HTTP age or episode continuity cannot extend it.

ADSBDB's route incorporation restriction rules out silently caching its database.
Local user-licensed records exercise independent non-gating enrichment, with
unknown routes explicit. No paid service/credentials added. Launch schedules,
first GP/DECAYING and TIP do not establish local plume/reentry visibility. The
matrix preserves the blockers and future trajectory/geographic evidence required.
No general venting/fuel-dump source was established.

## Phase 4 implementation research — 2026-10-10

The existing small server-rendered stack justified Leaflet rather than a GPU/WebGL
SPA or paid map API. [Leaflet's BSD-2-Clause license](https://github.com/Leaflet/Leaflet/blob/v1.9.4/LICENSE)
was inspected before vendoring its 1.9.4 JS/CSS; the notice is retained. This is
justified reuse of a standard map library, with independent All Sky Alert logic.
[OSM tile terms](https://operations.osmfoundation.org/policies/tiles/) informed the
optional human-viewed layer, attribution, origin Referer and no-prefetch policy.
Automated QA uses a blank base map. [TOML Kit](https://tomlkit.readthedocs.io/en/latest/)
preserves configuration comments/style; validated atomic save and revision guards
remain application-owned. No referenced-project implementation was copied.

The [NOAA public HRRR dataset](https://registry.opendata.aws/noaa-hrrr-pds/) and
[natural-level inventory](https://www.nco.ncep.noaa.gov/pmb/products/hrrr/hrrr.t00z.wrfnatf00.grib2.shtml)
support selective indexed byte ranges. A direct live index check found SPFH/PRES,
not a natural-level RH record. [NCEP moisture](https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/grib2_table4-2-0-1.shtml)
and [cloud](https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/grib2_table4-2-0-6.shtml)
tables establish cloud-ice mixing ratio kg/kg and ice number kg⁻¹. ecCodes may
label these newer parameters unknown with HRRR's legacy table version; numeric
parameter identity and explicit table units are verified rather than guessing.
[ecCodes Python](https://github.com/ecmwf/eccodes-python) provides an optional
portable decoder; Windows public range/profiles and Linux frozen-field checks are
recorded separately from offline unit tests.

[Atmospheric Optics CZA geometry](https://www.atoptics.co.uk/blog/cza-effect-of-solar-altitude/)
provides the constrained Sun-altitude screen. Geometry eligibility is physically
motivated, whereas our cold-layer/RH/ice and surface diamond-dust thresholds remain
uncalibrated engineering heuristics. Sparse levels cannot establish a complete
cloud layer, optical depth, orientation or crystal shape. Those unknowns are
preserved in both detector evidence and user explanations.
