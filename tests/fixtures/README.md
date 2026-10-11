# Phase 3 frozen fixtures

- iss-omm.json: frozen numeric ISS OMM example documented by Skyfield's satellite
  guide, 2024-05-06. Historical inputs only; runtime never uses this fixture.
- adsb-radius.json: invented representative readsb v2 radius payload; no real
  flight route or personal aircraft history is incorporated.
- iss-transit-reference.json: physical SGP4 cases using the frozen ISS inputs,
  sites chosen on nominal Sun/Moon centerlines with the independent calculation
  in tests/orbit_reference.py. These are not observed transits or provider-site
  forecasts. That path uses direct SGP4 TEME, UTC GMST, WGS84 observer and Astronomy
  Engine 2.1.19 topocentric body vectors with separate dense scan/bisection. Sun
  and Moon both above horizon in daylight; Moon duration is <1 second.
- vallado-omm.json: modern OMM transcription of Vallado's test case 5; selected
  TEME verification vectors from sgp4's MIT-distributed tcppver.out. Tests compare
  1e-5 km position / 1e-8 km/s velocity. This small numerical fixture is justified
  independent validation, not copied propagation code.

References: [Skyfield guide](https://rhodesmill.org/skyfield/earth-satellites.html),
[Vallado verification publication](https://celestrak.org/publications/AIAA/2006-6753/),
[sgp4 distribution](https://pypi.org/project/sgp4/).

Phase 4: `hrrr-ice-level20.grib2` is one unmodified 54,788-byte CIMIXR hybrid-level
20 record from NOAA HRRR, initialization 2026-10-10 12Z, forecast +3 hours.
Public source:
`https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.20261010/conus/hrrr.t12z.wrfnatf03.grib2`
with byte range 283953137–284007924 located through its `.idx`. SHA256:
`95c773ba531e6a1ca5189f794d1623bc417d5873825803cad9c98cf379d28e17`.
This public federal weather data fixture independently validates ecCodes numeric
parameter identity, documented kg/kg units, model time, level and nearest-grid
geometry. It contains no credentials, copied project code or aircraft history.
Normal tests never fetch it; runtime never reads test fixtures.
See [NOAA public dataset](https://registry.opendata.aws/noaa-hrrr-pds/) and
[NCEP moisture parameter table](https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/grib2_table4-2-0-1.shtml).

Notice retained for the transcribed sgp4 numerical verification data:

The MIT License (MIT)
Copyright © 2012–2016 Brandon Rhodes

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
