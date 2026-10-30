# Source capability matrix

## Jupiter module — 2026-10-28

The optional [Jupiter detector](jupiter.md) uses the existing MIT-licensed
[Astronomy Engine](https://github.com/cosinekitty/astronomy) for local Galilean
vectors, light-time and rotation-pole geometry, with NASA one-bar radii and
System II rotation constants. It adds no runtime requests or dependencies.
The [dated GRS estimate](https://skyandtelescope.org/stargazing-and-observing/interactive-stargazing-tools/transit-times-of-jupiters-great-red-spot/)
is configurable and expires independently of local moon ephemerides; it is not a
live measurement. Shared Open-Meteo/ECCC acquisition supplies observing quality.
Independent [Guide 9.1 event times](https://projectpluto.com/jevent.htm) are offline
test references only. Approximate center/umbra/shadow-center planning geometry is
explicitly distinct from finite-moon/photometric contacts; see the method limits
and measured reference differences in the Jupiter guide.

## Phase 4 decisions — 2026-10-10

Latest implementation status supersedes the historical surveys below.

| Source/library | Current use | Limits |
| --- | --- | --- |
| [NOAA HRRR public AWS](https://registry.opendata.aws/noaa-hrrr-pds/), [inventory](https://www.nco.ncep.noaa.gov/pmb/products/hrrr/hrrr.t00z.wrfnatf00.grib2.shtml) | Implemented optional sparse CONUS natural-level microphysics. Indexes select only CIMIXR/NCCICE/CLMR/TMP/SPFH/PRES/HGT records; exact HTTP byte ranges, persisted extracted-run cache | Default levels 20/30 at +3/+6h, synoptic run after publication lag, max 32 MB/run and 8 MB/record. Only nearest point within 20 km. RH derived from SPFH/PRES/TMP; not an assumed unavailable native RH record. Forecast run/sample/retrieval/expiry separate. Missing data use coarse fallback |
| [ecCodes Python](https://github.com/ecmwf/eccodes-python) | Optional `hrrr` extra, tested 2.49.0 on Windows and Linux. Numeric NCEP parameter identity handles HRRR legacy table-version metadata | Explicit units, level/run/forecast verification and physical bounds. NCCICE is kg⁻¹. No ice habit/orientation/optical depth claimed; frozen public GRIB test validates mapping |
| Existing shared Open-Meteo | Added surface temperature/dewpoint to combined acquisition, consumed by optics and separate surface diamond-dust proxy | Cloud layers remain coarse point forecasts; high cloud does not prove a halo. No per-detector weather calls |
| [Leaflet 1.9.4](https://leafletjs.com/download.html), [license](https://github.com/Leaflet/Leaflet/blob/v1.9.4/LICENSE) | Self-hosted JS/CSS, BSD-2-Clause notice retained; reusable map dependency only | Browser reads our bounded ADS-B snapshot. Trails separate from event episodes; routine craft shown without alerts |
| [OpenStreetMap tiles](https://operations.osmfoundation.org/policies/tiles/) | Optional human viewport tiles, attribution, HTTPS and origin Referer | Browser caching, no bulk/prefetch/offline downloads. No SLA; watched region disclosed. Custom provider must have acceptable terms/attribution; secrets prohibited |

Ice optics now have local geometry screens and explainable coarse opportunities;
the earlier future-only HRRR/halo statements are historical. Full vertical profiles,
directional obstruction, crystal orientation/habit and calibrated optical occurrence
remain deferred. See [operation](phase4-operation.md) for all current configuration,
forecast coverage and fallback details.

## Phase 3 decisions — 2026-10-09

The Phase 2 survey below remains as research/backlog context; implementation status
is superseded here and in [Phase 3 verification](phase3-verification.md).

| Source | Phase 3 use | Limits / freshness / terms |
|---|---|---|
| [CelesTrak GP JSON](https://celestrak.org/NORAD/documentation/gp-data-formats.php), [policy](https://celestrak.org/usage-policy.php) | Implemented minimal per-catalog acquisition for ISS/configured objects. Current/first GP enables propagation after cataloging; DECAYING identifies candidates, not local reentry visibility | Public, no credentials. Shared persistent >=7200s cache; non-200 stops HTTP globally until explicit reset; network/validation backoff >=cache cadence. EPOCH warning/stale/invalid 24/48/96h; transit <=12h current, <=24h target. No generalized redistribution license assumed |
| [Skyfield satellites](https://rhodesmill.org/skyfield/earth-satellites.html), [sgp4](https://pypi.org/project/sgp4/), [skyfield-data](https://github.com/brunobord/skyfield-data) | Installed MIT dependencies: Skyfield 1.55, sgp4 2.27, packaged DE421 from skyfield-data 7.0.0. Real consumers now justify NumPy/kernel costs | Offline propagation, immutable kernel SHA-256/version, conservative 1900–2053 coverage, built-in time tables. Numerical roots do not quantify physical uncertainty; GP has no covariance. Asset credits JPL; unused packaged IERS is not loaded |
| [ADSB.lol](https://www.adsb.lol/docs/open-data/api/), [operator](https://github.com/adsblol/api), [readsb fields](https://github.com/wiedehopf/readsb/blob/dev/README-json.md) | Implemented observer radius acquisition, normalized states and episodes. Probe successful; position time from millisecond v2 now minus second seen_pos | ODbL 1.0, attribution retained; review obligations before derived-database publication. Dynamic limits; 15s cache, position stale 30s/invalid 90s, persisted Retry-After/backoff. No PIA/LADD recovery or inferred routes |
| [OpenSky REST](https://openskynetwork.github.io/opensky-api/rest.html) | Evaluated alternative, no adapter. Position time_position separate from last_contact; no ordinary route schedule | Anonymous 400 credits/day insufficient for 15s use. Standard 4000/day, feeder 8000/day, small box 1 credit; OAuth2 authenticated tiers. Account/data terms need review |
| [Airplanes.live API docs](https://airplanes.live/api-docs/), [terms](https://airplanes.live/terms-of-use/) | Current api-docs endpoint found; search-rendered spec describes point/radius, Apache-2.0. Direct pages expose little static content; endpoint access failed here | Spec/software license is not blanket dataset permission. Feeder access, data terms/quota not verified sufficiently for selection. No silent alternate-source fallback |
| [ADSBDB](https://github.com/mrjackwills/adsbdb), [FlightAware personal terms](https://www.flightaware.com/commercial/aeroapi/AeroAPI_Personal_License_Jan2025.pdf) | ADSBDB route incorporation requires permission. Commercial licensed services possible later; no subscription/key or route DB used. Implemented local user-licensed records only | Non-gating, aircraft+callsign bound, separate update/expiry/rights. 3600s cache, 24h age default, <=7d explicit validity. Unknown routes stay unknown; heading does not imply an airport pair |
| [Launch Library 2](https://thespacedevs.com/llapi) | Schedules/status/NET/window/update evaluated; no trajectory establishing local visibility in schedule schema. No unused runtime adapter | Public 15 requests/hour unauthenticated, higher paid/authenticated tiers. Media/data redistribution terms need review. Future ingestion must track last_updated separately from NET; no schedule-only visible plume claim |
| [Space-Track docs](https://www.space-track.org/documentation) | Optional authenticated GP/TIP evaluated; no account/secrets used. GP duplicates current required input; TIP can add uncertainty windows/regions later | <30/min, <300/h, GP hourly, TIP hourly or 10min within 12h of reentry; account/release terms. TIP location describes descent at 10km, not an impact point or proven local sighting |

Newly cataloged configured objects use the shared GP/pass path. Their sunlit geometry
has unknown brightness; no generic spacecraft visual detectability is claimed.
Launch plumes, fuel dumps, upper-stage venting and spirals still lack a defensible
general structured source/trajectory. No social-media scrape or schedule/decay
heuristic is installed. A future explicit published-trajectory/manual adapter is
preferable. These remain documented acceptance gates, not empty modules or polling.

Runtime sources added: GP, ADSB.lol, optional local enrichment and versioned local
orbital/kernel health. Epoch, position and enrichment ages are independent from
HTTP success. Scientific expiry prevents alerts when workers/network stop. ISS
profiles reuse weather; aircraft does not require it.

## Phase 2 survey (historical)

Research date: 2026-10-08 (America/New_York). Operational checks are one-off public,
credential-free requests; HTTP 200 proves access at that instant, not a service SLA.
Probe timestamps in test-artifacts/source-probes.json are UTC (2026-10-09 early UTC
is still October 8 locally). No route database or third-party code is copied.
"Not established" below is deliberate: no published numeric limit/licence/cadence
was found in the cited documentation; it must not be invented.

## Capabilities, consumers and acquisition decisions

| Source / authoritative documentation | Raw capabilities and nature | Consumers | Cadence / freshness definition | Shared cache policy proposed or implemented | Credentials / cost | Limits, coverage, failure modes and fallback |
|---|---|---|---|---|---|---|
| [Astronomy Engine](https://github.com/cosinekitty/astronomy/blob/master/source/python/README.md), [MIT licence](https://github.com/cosinekitty/astronomy/blob/master/LICENSE) | Local Sun/Moon/planet geometry, illumination, local solar/global lunar eclipse searches, jovicentric moon vectors | Existing planets/sunset; eclipses; aurora Moon/darkness; future occultations/Jupiter | Installed library version and supported calculation range, not HTTP age | Exact position calls bounded in-memory; durable detector batches with settings/version/horizon key | None; installed 2.1.19 | No arbitrary comet orbit/SGP4 service. Contact/limb terrain, timing uncertainty and eclipse horizon clipping require our logic. Prefer local calculations. Operational locally; no copied library implementation. |
| [Skyfield](https://rhodesmill.org/skyfield/earth-satellites.html), [planet kernels](https://rhodesmill.org/skyfield/planets.html) | Local SPK ephemerides, modern OMM to SGP4, topocentric satellite geometry, illumination | Future shared Earth orbit: ISS, rocket bodies, post-launch payloads; optional precision solar-system backend | Kernel identity/coverage and orbital epoch separately from download time | Immutable kernel by checksum/version; shared OMM sets at source cadence | Library free MIT; no account for local compute | Ephemerides are finite-range assets; OMM lacks covariance and cannot guarantee sub-second transit accuracy. No live network during propagation. Not installed until a consumer is implemented. |
| [CelesTrak GP JSON/OMM](https://celestrak.org/NORAD/documentation/gp-data-formats.php), [usage](https://celestrak.org/usage-policy.php) | Catalog identifiers and mean orbital elements/epoch, predicted propagation inputs; ISS/new spacecraft/rocket-body groups | ISS transits, spacecraft passes, post-launch groups | GP publishes every 2h; physical freshness uses EPOCH, not HTTP date; epoch can remain older after retrieval | One minimal object/group query per 2h update, shared across all orbit consumers; no blind retry loops on unchanged-data 403 | Public; no credentials; no paid service required | Live ISS JSON 200. Six-digit catalog numbers need OMM/JSON. Errors/unchanged data can be 403; repeated errors can cause firewall blocking. Orbit manoeuvres/drag undermine old elements. No inferred fuel-dump schedules. Attribution/usage policy retained; blanket redistribution licence not established here. |
| [NOAA SWPC OVATION](https://www.spaceweather.gov/products/aurora-30-minute-forecast), [JSON](https://services.swpc.noaa.gov/json/ovation_aurora_latest.json) | Global geographic auroral model grid, Observation Time, Forecast Time, grid intensity/viewing-proxy percentage | Aurora; shared space-weather capability | Near-real-time model product; use both upstream timestamps. Lead is normally solar-wind travel time and can vanish in Kp fallback | Implement 5-minute retrieval cache; data-age gate 30min, invalid after 60min; forecast valid window checked independently | Public; no account/key | Live 200, 65,160 cells. Not a local visual probability. Solar-wind contamination/outage can switch forcing to Kp. Both hemispheres; severe-storm/model uncertainty. No trustworthy observer prediction without fresh oval. Numeric rate quota not documented; conservative caching/backoff. |
| [SWPC data products](https://services.swpc.noaa.gov/products/), [planetary Kp](https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json), solar-wind plasma/mag feeds | Timestamped observed/estimated indices and L1 solar-wind/Bz series | Secondary aurora explanations, future space-weather detectors | Kp intervals and solar-wind sample times are distinct; inspect rows/nulls and upstream gaps | Same SWPC service, independent product TTLs; minutes for wind, slower for Kp. Only retrieve a product when a consumer uses it | Public; no key | Kp probe 200. Kp is a 3h index, not a sky elevation. No secondary feed substitutes an arbitrary Kp-to-visibility rule. This run uses OVATION only; missing secondary metrics are disclosed rather than downloaded unused. |
| [NOAA data terms](https://www.weather.gov/disclaimer) | US government forecast/data reuse terms | SWPC/HRRR UI and exports | Not a data-age source | Attribute NOAA; label our derived interpretations | No paid data dependency | Government data generally public; preserve third-party notices and do not imply NOAA endorsement. |
| [Open-Meteo](https://open-meteo.com/en/docs), [terms](https://open-meteo.com/en/terms) | Hourly modeled cloud/layers, humidity, visibility, winds; no ice habits or horizon rays | Most visual events, existing planets/sunset; aurora/eclipses | Retrieval cache plus valid time; upstream model initialization not supplied by current adapter | Existing 60min TTL/6h stale fallback, one combined location request | Free noncommercial API; commercial use has separate terms | CC BY 4.0 attribution. Free limits stated in terms (10,000/day, 5,000/hour, 600/min at research); current hourly use is modest. Geographic model selection and horizon vary; do not invent directional cloud obstruction. Existing implementation tested live. |
| [ECCC RDPS/GeoMet](canadian-seeing.md) | Native seeing categories and broader weather model fields | Planets; future Jovian/occultation detail | 00/06/12/18UTC runs, hourly fields to 84h; issue/valid/retrieval distinct | Existing shared per-run/hour/location cache; 18h maximum run age | Public; no key; ECCC Data Services End-use Licence | Regional 10km field, index 0 cloud masked; no fake arcseconds. Existing point validation/cache retained. Numeric quota not established; bounded requests/backoff. |
| [HRRR NOAA](https://rapidrefresh.noaa.gov/hrrr/), [NODD registry](https://registry.opendata.aws/noaa-hrrr-pds/), [NOMADS subset](https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl) | 3km modeled vertical temperature/humidity/heights/winds, condensates, cloud cover; hourly forecast, not observed crystal habit | Atmospheric optics; future richer common weather | Model init + forecast valid time + retrieval. Hourly cycles; CONUS/Alaska products differ; horizon depends cycle/product (usual 18h, extended CONUS synoptic cycles 48h; verify per run) | Proposed selected .idx entries + HTTP byte ranges and point/region extraction, or documented regional subset; immutable run/field/level cache | Public anonymous NOAA data, no AWS account; own compute/storage costs | Live natural-level .idx 200: **CIMIXR**, **NCCICE**, **CLMR**, **NCONCD**, TMP/RH/HGT/VVEL present. Ice mass/number are not orientation, plate habit or optical depth. Generic CICE/ICMR names would miss this product. No full national GRIB downloads. Missing run/field/206 support/decoder metadata fail closed; cloud-layer-only fallback must be labeled coarse opportunity. |
| [ADSB.lol API](https://www.adsb.lol/docs/open-data/api/), [operator README](https://github.com/adsblol/api/blob/main/README.md) | Observed aircraft/MLAT positions, callsign, ground speed/track, altitudes, squawk, optional readsb enrichment fields | Current aircraft, short projected proximity, configured interesting-aircraft rules | Feed `now`, individual `seen_pos` and position age, not simply retrieval. Proposed polling ≥15s | One observer radius query, short TTL; track episodes persist for dedup; enrichment separate, slower | Public/no key now; operator notes future feeder API keys; free | Live radius query 200. ODbL 1.0; preserve attribution/share-alike obligations if publishing a derived database. Limits dynamic, no fixed documented quota. Coverage follows receivers; missing/MLAT positions, `ground` altitude, stale tracks, callsign reuse. Straight-line forecasts only with stated uncertainty. Selected for a later dedicated fast-lane implementation. |
| [Airplanes.live](https://airplanes.live/), [point endpoint](https://api.airplanes.live/v2/point/51.48/0/10) | Candidate readsb aircraft API, overlapping positional capabilities | Alternative aircraft source | Need documented response/sample semantics before selection | No runtime calls implemented | Credential/quota/licence terms not verified in this research | Probe returned **403**; API-guide URLs were unavailable/404. This is access failure from this environment, not proof of global outage. No invented API availability/licensing; ADSB.lol is the evaluated fallback candidate. |
| [OpenSky REST](https://openskynetwork.github.io/opensky-api/rest.html) | State vectors, tracks and retrospective flight records; inferred endpoints are not filed/live routes | Alternative position source, research/history | `time_position` vs `last_contact`; anonymous 10s/registered 5s time resolution | Proposed bounding-box state cache; respect per-endpoint credit budgets | Anonymous live states 200; OAuth2 client credentials for authenticated tiers, separate secrets | Anonymous 400 credits/day; standard 4,000/day; feeder 8,000/day; box ≤25 square degrees costs 1. Quotas are too small for continuous anonymous 15s polling. Basic auth obsolete. Research/commercial rights need OpenSky terms/licensing review; no blanket redistribution assumed. Null states/position/estimated origin. |
| [ADSBDB operator documentation](https://github.com/mrjackwills/adsbdb) | Aircraft/airline and callsign route **database enrichment**, not observed positional truth | Optional aircraft names/routes | Database revision/retrieval; route can be outdated or callsign reused | No persistence adapter added until route-data permissions settled | Public probe 200, no key; numeric contract quota not established | Operator README restricts copying/publishing/incorporating route data without permission from David J Taylor. Do not silently persist a route DB or describe endpoints as confirmed. Position works without enrichment. Software licence is not data permission. |
| [Launch Library 2](https://thespacedevs.com/llapi), [current docs](https://lldev.thespacedevs.com/docs) | Launch/event IDs, NET/window/status/pad/mission/record update, not continuous launch trajectory | Launch awareness; inputs to visibility when a trajectory exists | Retrieval plus `last_updated`; NET is a scheduled time, not data timestamp | Proposed 30–60min schedule cache, accelerate within 15 requests/h budget near launches | Public 15/h unauthenticated; higher quotas paid/authenticated, not approved or added | Live 2.3.0 root 200; development endpoint intentionally stale. Source free access is not blanket image/data redistribution permission: media carry separate licences; full data licence not established here. Reschedules, scrubs, unknown azimuth/path. Schedule alone must not create "visible launch". |
| [Space-Track documentation](https://www.space-track.org/documentation) | GP, SATCAT, decay history/60-day estimates, TIP windows | Optional reentry/catalog domain, not Gmail | Orbital EPOCH; TIP insert/message vs decay epoch; broad window uncertainty | GP hourly maximum; TIP hourly (10min when reentry within 12h); 60-day weekly, per docs | Account/login required; no credentials requested/read; authenticated API not probed | <30 queries/min and <300/h plus product-specific caps. US user-agreement/release/redistribution obligations must be reviewed for an account. Do not turn TIP time windows into precise local glowing-reentry predictions. Public docs accessible; authenticated availability unverified. |
| [JPL Horizons](https://ssd-api.jpl.nasa.gov/doc/horizons.html), [fair use](https://ssd-api.jpl.nasa.gov/doc/index.php) | Computed observer/vector/element ephemerides incl. comets/Galilean satellites; SPK generation for small bodies | Independent validation, comet orbit assets, high-precision Jovian backend | Orbit solution ID/epoch or kernel validity, not recent observation merely because API returned 200 | Batch dates, reuse immutable ephemerides; one simultaneous JPL request only | Public no key/free; application/contact User-Agent required | Documentation operational. No fixed numeric quota given; cache, serial requests, backoff. Returns text result inside JSON, ambiguity/errors, time-scale/frame pitfalls; avoid one call per geometric sample. Prefer existing local engine for Sun/Moon/planets. |
| [JPL SBDB](https://ssd-api.jpl.nasa.gov/doc/sbdb.html) | Small-body orbit solution/covariance/physical parameters when available; comet H/K brightness parameters can be absent | Comet discovery/selection plus Horizons/local propagation | Orbit epoch/solution date/observation arc, not HTTP age | Proposed daily catalog filter + per-object solution-key cache | Public no key; same JPL serial fair-use policy | 1P query 200. Predicted brightness is not reported/observed magnitude. Geometry, elongation and Moon screening required. Nongravitational/fragmenting comet updates and missing parameters. No magnitude-only alerts. |
| [MPC comet elements](https://minorplanetcenter.net/iau/Ephemerides/Comets/Soft00Cmt.txt), [formats](https://minorplanetcenter.net/iau/info/CometOrbitFormat.html) | Current orbital elements/epoch and magnitude-law inputs, **catalog**, not reliable real-time photometry | Comet catalog discovery, shared orbit service | File/catalog semantics + per-orbit epoch; HTTP retrieval is not latest observing date | Proposed daily conditional/file cache; separate revised photometry input later | Public file 200 (~164KB); no credentials | Numeric limits/reuse terms not established in examined format docs; verify before redistributed catalogs. Website access can be 403 while file works. Missing H/K, uncertain coma/brightness, format changes. SBDB/Horizons complement, not a substitute for actual brightness observations. |
| [Sky & Telescope GRS tool](https://skyandtelescope.org/stargazing-and-observing/interactive-stargazing-tools/transit-times-of-jupiters-great-red-spot/) | Published dated System-II GRS longitude/drift reference, **not a verified structured API** | Future configurable GRS calculation | Reference observation date + drift/uncertainty age | Proposed user-configured longitude/date/source; no scraper or timeless constant | Website readable; no runtime API assumed | Current published reference is dated, not permanent truth. No machine-readable feed/redistribution contract verified. Disable stale longitude; do not copy JavaScript calculator. |
| [USNO eclipse API](https://aa.usno.navy.mil/data/api), [solar computer](https://aa.usno.navy.mil/data/SolarEclipses) | Locally computed eclipse contacts/obscuration and catalog; independent validation | Tests/reference for local eclipse backend | Algorithm/date coverage, not live freshness | Validation fixtures, no runtime dependency | Public; numeric quota not established | Solar computer documents 2017–2026; older API page gives narrower range. Do not assume perpetual forward coverage. UT1/UTC distinction; partial below-horizon phases. |

## Data gaps and implementation implications

- Route truth and enrichment are separate: ADS-B does not disclose a confirmed
  origin/destination. ADSBDB permissions are a real integration constraint.
- Ice mass/number in HRRR justify a *narrow* future vertical-profile adapter, but
  no model field found establishes crystal orientation/shape. CZA geometry can be
  calculated locally; [optical geometry reference](https://atoptics.co.uk/blog/cza-effect-of-solar-altitude/)
  constrains Sun elevation, not crystal occurrence. Pillars/diamond dust need
  near-surface ice and local temperature/profile validation, not just high cloud.
- GP orbit data enable local SGP4, not sub-second accuracy claims by themselves.
  ISS transit work needs independent contacts/near-miss fixtures, propagation-error
  checks and an orbital-age gate considerably stricter than routine satellite passes.
- No reliable general structured venting/fuel-dump/spiral timetable was established.
  Published trajectory/manual-source assets can be future explicit inputs. An absent
  source is not a prediction opportunity.
- OVATION permits an observer-relative **horizon opportunity model**, not a promise
  of visual detectability. Emitter height, low-elevation extinction, Moon and cloud
  uncertainty remain distinct from model grid intensity.
- GRS needs a dated configurable reference. Comet predicted vs measured brightness
  requires separate records; this research did not find an adequate blanket real-time
  brightness observation API to silently treat catalog H/K as observations.

In the Phase 2 run only weather/seeing, shared local ephemeris, and OVATION acquired/computed
runtime data. Other entries then documented evaluated capabilities and future
contracts, not implemented adapters. No paid dependency or authenticated service
has been introduced.

OVATION validity is an **application ±20-minute opportunity window**, not a native
NOAA forecast-duration field. Default routine eligibility ends at the earlier of
observation+30min and window end; invalid data means observation age>60min,
future observation time, expired window or unavailable/invalid payload. A cached
forecast can remain usable during transport backoff if its product timestamps
still qualify. Forecast lead varies: the operational probe had 75 minutes, not a
hardcoded 30-minute lead. Frozen UI/event records retain the state at evaluation;
current source-health views age dynamically.
The latest OVATION target time normally remains in the future; it is not a storm
onset/duration estimate. Now/active only applies if the supplied forecast's
application window includes the present. Scientific expiry can precede that
window's start when forecast lead is long, deliberately requiring updated inputs
before arrival. No current-sighting claim or extrapolated nowcast is made.
