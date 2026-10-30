# Jupiter observing events

## Design

Jupiter is an explicit, optional detector in the existing slow worker lane. It
adds moon phenomena and Great Red Spot (GRS) viewing windows alongside the existing
nightly planetary windows. No new scheduler, atmospheric downloader, storage
schema, delivery path or runtime dependency is needed.

The local Jovian ephemeris adapter uses the installed Astronomy Engine's Galilean
moon vectors, Jupiter pole and light-time solution. The detector intersects
geometric phenomena with observer altitude and darkness, then emits ordinary
Candidates. The shared `planetary` assessment evaluates cloud obstruction,
Canadian ordinal seeing (or explicitly uncalibrated wind fallback), transparency
and altitude. Weather changes quality, never intrinsic interest or identity.

Moon transits, occultations, eclipses and shadow-center transits remain separate
phenomena. Additional overlap windows identify simultaneous activity, including
GRS viewing, without inflating every single event's interest. Only overlaps that
occur during the local observing window count. A hidden moon is not described as
visible. Numerical boundary refinement is not a claim of physical contact accuracy.

GRS predictions use System II rotation with a dated longitude and linear drift,
not System III magnetic rotation. Its reference has an explicit expiry. Expired
GRS data cannot create opportunities or qualify queued notifications, while moon
events continue normally. References, assumptions and nominal uncut intervals
are available on the event detail page.

The detector owns defaults and validation; registry registration supplies its
family label, settings enable switch, Gmail family selection and atmospheric
consumers. Persisted geometry batches reuse the existing cache. All normal web
requests read predictions and never calculate geometry or acquire weather.

## Research references

- [Astronomy Engine Python API](https://github.com/cosinekitty/astronomy/tree/master/source/python):
  `JupiterMoons` gives jovicentric EQJ vectors; `BackdatePosition` exposes the
  light departure time. The existing dependency is MIT licensed
  ([license](https://github.com/cosinekitty/astronomy/blob/master/LICENSE)).
  This module calls its public API; no upstream implementation code is copied.
- [NASA Jupiter radii](https://nssdc.gsfc.nasa.gov/planetary/factsheet/jupiterfact.html):
  equatorial 71,492 km, polar 66,854 km at the one-bar level.
- [NASA PDS longitude systems](https://atmos.nmsu.edu/jsdap/encyclopediawork.html):
  System II prime meridian 43.30 degrees at J2000, rotation 870.270 degrees/day.
- [Sky & Telescope GRS reference](https://skyandtelescope.org/stargazing-and-observing/interactive-stargazing-tools/transit-times-of-jupiters-great-red-spot/):
  91 degrees System II on 2026-06-01, drifting +1.75 degrees/month. This is a
  published extrapolation, not a current observation; other longitude references
  can disagree. Do not scrape the calculator or treat it as a permanent truth.
- [Project Pluto / Guide 9.1 event tables](https://projectpluto.com/jevent.htm)
  provide independent minute-scale reference checks, not a runtime dependency.
  No Project Pluto source code is incorporated.

## Enable and configure

Existing configurations default `detectors.jupiter.enabled` to false. In Settings,
enable **Jupiter moons / Great Red Spot**, save and restart the service. The
**Jupiter Great Red Spot (dated reference)** switch can independently disable GRS;
moon phenomena continue. The example config enables the module and contains all
defaults. Do not replace personal configuration with the example.

For manual changes, stop the service before editing `config.toml`:

```toml
[detectors.jupiter]
enabled = true
minimum_altitude_deg = 30
maximum_sun_altitude_deg = -6
aperture_mm = 203.2
phenomena = ["transit", "shadow_transit", "eclipse", "occultation"]
include_overlaps = true
```

Omitted keys take validated defaults. `horizon_days` defaults to 14 (maximum 31)
and also respects `service.prediction_days`. Geometry is cached for six hours;
the shared planetary assessment is updated each slow cycle. Higher altitude,
less cloud and better native seeing increase observing quality. Observing quality
is an uncalibrated suitability score, not a probability of resolving detail.
Telescope aperture supplies a diffraction reference; cooling, collimation and
optical quality are not modeled. The existing broad nightly planetary window
remains useful even when no special moon/GRS phenomenon is visible.

PowerShell launch from the repository:

```powershell
.venv\Scripts\python.exe -m all_sky_alert --config config.toml check
.venv\Scripts\python.exe -m all_sky_alert --config config.toml serve
```

Open **http://127.0.0.1:8008/**. On the Pi, use the existing native venv/systemd
deployment; there are no extra packages, large kernels or GPU requirements.
Select the Jupiter family and use observing-quality sorting to find favorable
conditions, or retain the default chronological/newest-first order. Details show
nominal intervals, approximate focus times, geometry, seeing/cloud inputs,
sources, reference expiry and the normal alert history.

### Great Red Spot reference maintenance

The bundled published estimate is 91 degrees System II at 2026-06-01 00:00 UTC,
drifting +1.75 degrees per 30.4375 days. `grs_window_minutes = 50` means 50 minutes
each side of the predicted central-meridian passage. This is a favorable viewing
window, not the full time the storm is on the visible hemisphere.

The conservative `grs_reference_max_age_days = 180` expires that reference on
**2026-11-28 00:00 UTC**. No GRS window extending beyond expiry is emitted. Once
the reference expires, moon predictions continue and their details report the GRS
reference as expired. Existing GRS-dependent predictions/queued deliveries are
bounded by Candidate scientific validity, including exceptional-interest policy.

With the service stopped, update `grs_reference_utc`, `grs_longitude_deg`,
`grs_drift_deg_per_month` and `grs_source_url` together from a dated, credible
System II observation/reference. Do not merely move the epoch forward while
retaining an old longitude. Configuration changes invalidate the geometry cache.
There is no runtime scraping or automatic claim that an extrapolation is a fresh
measurement. Different published references can yield appreciably different times.

### Alerts and persistence

The existing local notification policy and Gmail adapters apply automatically.
The Gmail family selector is `jupiter`; existing wildcard selectors include it
after the detector is enabled. No OAuth credentials or new mail transport are
needed. Routine Jupiter interest is capped at 88, below the default exceptional
weather-bypass threshold of 90; the existing configurable interest/quality/
confidence thresholds and per-kind overrides still govern alerts. If immediate
Gmail is configured with an interest floor of 85, most individual phenomena will
be visible in the UI/daily digest without immediate email.

Moon IDs use moon/phenomenon/orbit counts and observer/observing-night identity,
not weather or refined contact seconds. GRS uses a rotation count. Overlaps use
their constituent identities plus the boundary that starts the distinct segment;
the same pair before and after a third feature cannot collide. Eclipse and
occultation disappearance/reappearance each have separate identities. Existing
SQLite revisions, unique milestones and outbox claims prevent repeated delivery
on evaluation/restart. No database migration is required.

## Scientific scope and limitations

- Moon transits/occultations use a moon **center** crossing Jupiter's projected
  oblate one-bar limb. Finite satellite disks are not modeled; these are not
  first/second/third/fourth limb contacts. All four Galilean moons are calculated.
- Eclipse windows use the moon center within an approximately contracted oblate
  umbra for the finite Sun. There is no light-curve, gradual penumbral fading,
  mutual moon eclipse/occultation or limb-topography model.
- Shadow transits follow the Sun-center ray from the moon to Jupiter's oblate
  surface. Only an Earth-facing intersection is retained. Shadow disk size,
  penumbra and silhouette edge contacts are not calculated.
- Eclipse/occultation opportunities are preparation windows, default ±15 minutes
  around an observable disappearance/reappearance. A boundary masked by the other
  phenomenon is excluded. A coincident window requires the transition itself to
  occur during the other feature, not simply overlapping preparation times.
- Overlap events complement individual features. A continuous overlap gets its
  own window and higher intrinsic interest; a transition coincidence is labeled
  a coordinated viewing window. This can produce separate local milestones for
  an individual phenomenon and its more interesting combination. Gmail's floor
  and existing event policies control delivery volume.
- Geometry is sampled every five minutes and boundaries refined to a one-second
  numerical bracket. That tolerance does **not** establish physical accuracy.
  Selected Guide 9.1 comparisons are within four minutes, except a Callisto
  eclipse egress differing by approximately five minutes (six-minute regression
  tolerance). This is not a validated global error bound. Begin observing at
  least ten minutes before a predicted contact. Short/grazing events entirely
  between samples can be missed.
- Jupiter light travel time is retained. The sightline is topocentric; differential
  moon light time, differential aberration and topocentric correction to the
  light-time solution are omitted at this planning scale. Local terrain and
  horizon refraction uncertainty are not modeled. Altitude uses the geometric
  center; darkness uses the existing solar altitude adapter.
- Cloud/seeing are assessed near the selected event focus time, not continuously
  throughout the window. Beyond atmospheric coverage the assessment remains
  unknown. A wind fallback is explicitly an uncalibrated proxy, not physical
  seeing; ECCC categories remain ordinal, including category 0 cloud masking.
- Numeric occurrence/assessment confidence values are heuristic trust indicators,
  not statistically calibrated probabilities or contact uncertainties.

## Validation

`tests/test_jupiter.py` runs entirely offline: independent moon timing references,
independent central-meridian approximation, GRS longitude shifts and expiry,
observable/hidden transitions, genuine coincidences, repeated overlap segments,
visibility clipping, stable identities, cloud/seeing/altitude separation, shared
forecast request caching, restart/milestone deduplication, UI/source rendering,
email rendering without sending, validation and detector failure isolation.
The existing mobile browser smoke script includes a frozen real Jupiter overlap
detail page. All service tests use private fixture storage and no credentials.

Windows verification on 2026-10-28: **344 tests passed**, including 29 Jupiter
tests; seven existing dependency deprecation warnings. Headless Edge passed 60
page/viewport checks at widths 360, 390, 430, 768 and 1280, including Jupiter
details, sorting/filter persistence and atomic Settings save. A separate offline
31-day Greenwich calculation produced 67 unique candidates across all implemented
phenomena, including real moon/GRS combinations. This does not constitute Pi
hardware acceptance or on-sky validation.

The next scientific improvement is validated finite-satellite/penumbra contact
and light-curve modeling, with broader independent date/latitude references.
No change to providers, event persistence, shared atmosphere or delivery contracts
should be needed for that refinement.
