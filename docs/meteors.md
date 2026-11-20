# Known meteor showers

Enable **Meteor showers (three peak nights)** in Settings and restart. Existing
configurations leave the new module disabled until selected. New-install example
configuration enables it. For manual configuration, stop the service first and
add or edit this table, then restart:

```toml
[detectors.meteors]
enabled = true
interval_seconds = 21600
minimum_altitude_deg = 15
maximum_sun_altitude_deg = -12
```

The module covers Quadrantids, Lyrids, Eta Aquariids, Alpha Capricornids,
Southern Delta Aquariids, Perseids, Draconids, Orionids, Southern Taurids,
Northern Taurids, Leonids, Geminids and Ursids. Predictions use the normal
`service.prediction_days` horizon (seven days by default). They appear in the
ordinary prediction list, details and local alert inbox. Gmail selections use
the registered `meteors` family and the existing delivery policy; the default
immediate interest floor of 85 admits Quadrantids, Perseids and Geminids only
when the ordinary quality/confidence rules also qualify them. Lower-interest
showers can appear in daily digests or use a lower immediate interest floor.
There is no new weekly long-range capability declaration.

Each shower gets three **local observing nights**: the noon-to-noon night
containing its nominal maximum, the preceding night and the following night.
Dates label the evening that begins each night, including across New Year and
daylight saving changes. A night produces no opportunity if no sampled segment
has sufficient darkness and radiant elevation. One contiguous segment per night
is retained, preferring high radiants and less moonlight. Stable identities use
observer, shower, annual occurrence and observing date; weather never changes
those identities. Ordinary per-event milestone deduplication applies to each
night independently, so multiple nights may alert.

Nominal maxima and peak radiants are bundled numeric facts attributed to the
[International Meteor Organization 2027 Calendar, Table 5](https://www.imo.net/files/meteor-shower/cal2027.pdf).
IMO solar longitudes use equinox J2000. The local Astronomy Engine adapter solves
for the geometric Sun's J2000 ecliptic longitude each year rather than repeating
fixed month/day dates or using equinox-of-date longitude. Catalog J2000 radiants
are precessed/nutated to date for airless horizon calculations. Darkness and Moon
positions/illumination use the same local ephemeris. No catalog or weather
acquisition occurs in the detector. Provenance includes the bundled catalog
version, source URL, ephemeris library/version and calculation method.

The `meteors` assessment profile reads the existing shared **total cloud cover**
forecast at each night's representative time. The uncalibrated quality score is
`100 × cloud clearance × (0.65 + 0.35 × sin(radiant altitude)) × moon factor`.
The moon factor is `1 − 0.65 × illuminated fraction` while the Moon is above
the horizon, otherwise one. This is a visibility heuristic, not an expected
meteor count, physical limiting magnitude or probability. Missing/outside-forecast
clouds produce unknown quality; stale clouds cannot qualify routine alerts.
Only consumed weather/cloud deadlines apply; unrelated Canadian seeing does not
gate these events or trigger its acquisition. Geometry is cached, while cloud
quality is reassessed every slow cycle. Shower interests stay below the default
exceptional-weather-bypass threshold; configured policy overrides still apply.

These are approximate annual planning opportunities. The three nights do not
imply equal activity: a narrow peak can last only hours. Radiant drift across
the three nights, unusual dust trails, meteor storms, local terrain, light
pollution and directional cloud gaps are not modeled. Clouds are sampled at the
representative time, not averaged or optimized over the entire night. Geometry
uses 15-minute samples and conservatively retains eligible endpoints; numerical
solar-longitude resolution is one minute, not actual peak timing uncertainty.
The catalog requires editorial updates as shower knowledge changes; applying
nominal annual longitudes to another year does not add year-specific outbursts.

Offline tests compare Quadrantid timing against the independent IMO 2026/2027
calendar descriptions, exercise real Geminid geometry, three-night identities,
New Year/DST, daylight/radiant exclusions, clouds/moonlight/freshness, cached
geometry reassessment, milestone deduplication and standard UI rendering.
