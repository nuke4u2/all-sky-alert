# Moon occulting planets

Enable **Moon occulting planets** in Settings, save and restart. This optional
module defaults off for existing configurations. The new-install example enables
it. To configure manually, stop the service, add/edit this table, then restart:

```toml
[detectors.lunar_occultations]
enabled = true
interval_seconds = 21600
horizon_days = 90
bodies = ["Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune"]
minimum_altitude_deg = 10
maximum_sun_altitude_deg = -6
minimum_solar_elongation_deg = 20
preparation_minutes = 10
```

The geometry horizon is independent of `service.prediction_days`, with a maximum
of 366 days. The detector follows the existing slow lane and persisted geometry
cache. Each visible disappearance and reappearance becomes a separate scheduled
opportunity, with ten minutes of preparation and up to two minutes after its
contact interval. The completely hidden interval produces no observing alert.
A nominal partial overlap that never fully hides the planet produces one
partial-overlap opportunity instead. These events have stable observer/planet/
occurrence/transition identities and use the ordinary deduplicated alert stages.

Intrinsic interest is **89/100** for all supported planets: high enough for the
default immediate Gmail floor of 85, and below the default exceptional-interest
weather bypass of 90. The existing `planetary` profile supplies the same total
cloud cover, visibility/humidity transparency proxy, seeing and altitude
screening as planetary observing. Occurrence confidence remains separate from
observing quality. Seeing precedence remains physical arcseconds when supplied,
otherwise native ECCC ordinal categories, otherwise the explicitly uncalibrated
wind proxy. ECCC category 0 stays cloud-masked/unknown. Missing/stale atmosphere
and stale consumed seeing cannot qualify routine alerts. Ordinary configured
quality/confidence thresholds still apply; high interest does not guarantee an
alert at low altitude or in poor conditions. Explicit notification policy
overrides retain their existing behavior.

Conditions are sampled at the representative **visible contact time** for that
particular transition. Clouds or seeing at disappearance do not stand in for
reappearance an hour later. The shared provider refresh and seeing-hour caches
remain centralized; the detector reads no atmosphere and sends no mail. A cached
geometry batch is reassessed every normal slow cycle. UI reads never initiate
geometry calculation or source acquisition. The `lunar_occultations` registry
family is selectable independently in Gmail modes. Its declared long-range
horizon permits weekly planning summaries from persisted geometry, with unknown
future weather left unknown. This never extrapolates weather/seeing coverage.

The local solver uses the already packaged **JPL DE421** kernel, Skyfield and a
WGS84 observer. It applies topocentric parallax, light travel time and apparent
direction corrections. Built-in timescale tables avoid Earth-orientation network
requests; conservative search coverage is 1900–2053 with padding at the edges.
Outside coverage, detection fails explicitly instead of fabricating events or
withdrawing prior predictions. This local asset has library/version, kernel
hash and coverage provenance, rather than a live observation-age TTL. No new
runtime dependencies, credentials or schema migration are required.

The solver searches minima on a fixed three-hour grid, then refines nearby
minimum limb gaps, including crossings whose coarse samples are all outside the
lunar disk. Skyfield vectorized work is bounded to 256 times per batch. External
contacts solve separation = lunar radius + planetary radius; internal contacts
solve separation = lunar radius − planetary radius. Apparent radii use
[JPL volumetric mean radii](https://ssd.jpl.nasa.gov/planets/phys_par.html) and a
[1737.4 km lunar mean radius](https://ssd.jpl.nasa.gov/sats/phys_par/). Outer-planet
system barycenters approximate planet centers. All calculations are offline.

Contact/preparation windows are clipped to the configured **airless center
elevations of both Moon and planet**, Sun elevation and planet–Sun angular
separation. A visible preparation period or visible fully hidden interval alone
cannot authorize a contact opportunity. Contacts outside the limits stay listed
as unavailable in event details. The default Sun ceiling limits predictions to
night/twilight. A positive ceiling explicitly permits daytime opportunities;
these include the existing eclipse solar-safety guidance in UI and email. The
elongation floor is an additional geometric selection limit, not a guarantee
that pointing an optical instrument near the Sun is safe.

These are approximate planning contacts, **not precision occultation timing**.
Circular silhouettes omit lunar mountains/valleys, planetary oblateness, Saturn's
rings, satellite events, illuminated-limb morphology, terrain and differential
refraction. A near-tangent partial event can change with the omitted geometry.
The 0.1-second numerical solver tolerance does not measure physical accuracy;
displayed seconds are planning labels. Consult a specialist ephemeris and local
lunar limb model for precision recording. See the
[IOTA explanation of finite-planet partial occultations](https://occultations.org/publications/rasc/2026/nam26grz.htm).

Offline tests compare the October 6, 2026 Jupiter disappearance/reappearance at
three stations against independent IOTA/RASC center-limb predictions within one
minute. Further tests cover finite disk edges, short grazes between coarse
samples, near misses, partial overlap, elevation/daylight/elongation exclusion,
separate per-contact weather, Canadian seeing freshness/masking/precedence,
stable identity, cached geometry, alert deduplication, long-range unknown weather,
UI/email contact tables, local timezones and daytime solar-safety rendering.
