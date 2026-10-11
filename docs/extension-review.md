# Architecture review against future detectors

Review performed after the working slice. These are integration considerations,
not placeholder implementations or promises of scientific accuracy.

| Addition | Fits current boundaries | Additional work required |
| --- | --- | --- |
| Asteroid occultations | external catalog provider → detector → normalized candidate → profile | footprint/location uncertainty, precise star/asteroid geometry, seconds-scale timing; never use planetary sampling |
| Comets | ephemeris provider and faint-object profile | orbit/catalog freshness, magnitude uncertainty, Moon/AOD/extinction and horizon conditions |
| Meteor showers | catalog source, radiant geometry, faint-object profile | extended/multi-night windows and Moon/darkness; stable shower/year identity |
| Oppositions | local astronomy detector, intrinsic rarity and configurable far horizon | event search rather than observing-night grouping; distinct opposition and observing-window identities |
| Jupiter moon/shadow transits | domain ephemeris provider, precise detector, planetary atmosphere profile | satellite kernels, shadow/conjunction geometry, identity keyed by moon/occurrence; timing tolerance |
| ISS Sun/Moon transits | orbital and solar/lunar providers, independent geometry/profile | OMM epoch gating, second-scale search, path footprint and revised-pass matching; sunlight safety wording |
| Aurora | implemented OVATION source, height-scenario horizon geometry, Moon/darkness and common weather profile | validate observer heuristics; add directional clouds/extinction and independent storm episodes; no raw Kp-only map |
| Rocket launches | schedule/trajectory providers, local geometry/profile | external launch ID, reschedules/scrubs, explicit withdrawal; visibility cannot be inferred from schedule alone |
| Reentries | trajectory provider and uncertainty envelope | evolving time/location footprint and broad confidence ranges; exceptional importance bypass must remain explicit |
| Interesting ADS-B aircraft | feed provider, pure classification/trajectory detector, own profile | continuous track IDs, bounded time windows, new cadence/rate constraints; calls remain outside detector logic |
| Halos/sun dogs | optical-cloud provider and daylight profile | ice-cloud microphysics, angular solar geometry; clear-sky filtering must not be universal |
| Brilliant sunsets | sunset detector/profile already consumes layers differently | western illuminated cloud path, AOD/smoke, calibrated observations, possibly image/GOES data |

The review identified and fixed a notification risk: a safely failed delivery
could be retried using an obsolete favorable forecast. Dispatch now checks the
latest prediction/policy and cancels routine jobs that no longer qualify; retries
carry current forecast content with the original milestone identity.

It also confirmed a provider-failure risk: unexpected refresh failure must not
reuse a prior snapshot labeled fresh. Evaluation supplies explicitly unavailable
conditions in that case. Tests cover both behaviors.
Detector settings/defaults were moved to their modules, with registry.py shared
by configuration and evaluation. Adding the twentieth detector should not grow a
central detector-specific validator or require modifying unrelated modules.

The shared context contains current astronomy needs, but future data dependencies
are injected into detector constructors rather than accumulating arbitrary fields
in a giant context. Atmosphere is a service returning metric snapshots; additional
seeing/horizon providers require implementing that service's source composition,
not editing each detector. Existing profiles already accept a separate physical
seeing metric without treating cloud percentage as seeing.
Weather-triggered detectors may receive the already-prepared ConditionsSource
through constructor injection, allowing a halo detector to discover a candidate
from ice-cloud conditions without adding HTTP acquisition to its implementation.

Phase 2 review also checked noctilucent clouds, meteor fireballs, weather balloons
and bright satellite flares. Each can add a registered source/detector/profile
without editing unrelated detectors or delivery. NLC needs daylight/twilight and
mesospheric occurrence inputs; fireballs need actual observation timestamps and
episode association; balloons need track uncertainty, not aircraft-only rules;
flares need the future orbit service plus attitude/reflectance data. Metadata and
geometry remain extensible, and no universal darkness/clear-sky veto is introduced.

The scheduler now supports per-detector cadence inside independent slow/live lanes.
Adding a 15-second live consumer must not force aurora to recompute at that rate.
Source fetch cadence remains independent and unused sources are not requested.
Slow geometry batches are validated/persisted; live detectors read current
snapshots and their due times reset at restart. A future expensive live computation
needs its own bounded executor/lane; the current lightweight live lane is serial.
An adaptive scheduler or arbitrary dynamic lane names are not claimed here.

Review found and fixed two expiry risks: delayed routine alerts could outlive their
inputs while a worker stalled, and a shared seeing deadline could veto a profile
that ignored seeing. Per-input/profile deadlines now expire queued alerts using
only their consumed data. Tests cover both and provider failures preserve history.

It also removed a universal weather-status veto from core policy. Current weather
profiles enforce their own freshness and compose their own deadlines; future
observation-only profiles can ignore atmosphere. Scientific Candidate validity
is checked before any exceptional-interest weather bypass, including watch jobs.
Registry acquisition/ephemeris metadata makes actual source consumers explicit.

Deliberately deferred: cross-detector correlation, multiple observers, provider
fusion/precedence, tracked trajectories as a typed geometry union, minute/second
proximity-adaptive schedules, stage-specific material-change/cancellation email and validated
scientific uncertainty. A cancellation updates the UI and cancels pending delivery;
already-sent email is immutable. These features need actual consumers and tests
before expanding contracts. The current type metadata/geometry bags keep normal
extensions local, while changes to core envelope versions need architectural review.
