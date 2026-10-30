# Windows development only

Run these exact PowerShell commands from the project directory. Python 3.12 or
newer must be installed; the Windows `py` launcher avoids Store alias problems.

```powershell
Set-Location E:\AI\AgentProjects\all-sky-alert
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -c constraints.txt -e ".[dev,hrrr]"
if (-not (Test-Path -LiteralPath config.toml)) {
  Copy-Item -LiteralPath config.example.toml -Destination config.toml
}
notepad config.toml
```

Replace the **demonstration Greenwich coordinates and timezone** with your observer
latitude/longitude, elevation and IANA timezone (e.g. `America/New_York`). No personal
location is assumed. Longitude west of Greenwich is negative. Data paths resolve
relative to the configuration file, so the working directory does not determine
where your database is stored. Leave Gmail disabled initially.

```powershell
.venv\Scripts\python.exe -m all_sky_alert --config config.toml serve
```

Open **http://127.0.0.1:8008/**. Independent slow and live workers evaluate immediately;
the slow lane wakes every five minutes; the live lane wakes at the fastest enabled
detector cadence (15 seconds for aircraft, 300 seconds for aurora). Sources retain
independent cache/backoff. Delivery and scheduled milestones have their own bounded
15-second loop, without rediscovering geometry. Aircraft refresh through focused
JSON every 15 seconds; only the home page reloads every five minutes. Settings,
map and detail pages retain their state. Stop with Ctrl+C. “Now” and “Upcoming” events
default to event peak time, newest first; the filter form also offers interest,
observing quality, event type and priority sorting. Recent history defaults to events ending in the previous
two hours. Recent history fills naturally as the service runs: no fabricated past
predictions are added on first launch. Detail pages expose reasons, atmospheric
metrics, geometry, sources, alerts and recorded revisions. Eclipse details show
local contacts/visible phases and solar-viewing safety. Aurora details explain
bearing, elevation-height scenarios, darkness and Moon interference. Source health
ages observation/model timestamps on every page/API read, even if a worker stalls.
For [Jupiter moon and Great Red Spot events](jupiter.md), enable **Jupiter moons /
Great Red Spot** in Settings, save and restart. Existing private config is not
automatically changed. The example config contains the full optional Jupiter
table; its 8-inch aperture is only a diffraction reference. Update the dated GRS
longitude/drift/reference together in the TOML file with the service stopped.
Existing configurations do not automatically enable the new detectors: add their
tables below or use `config-sync`. The example enables them; preserve your observer
settings when upgrading. These instructions do not describe or alter your private configuration. Enable LAN/optional providers explicitly.

The recommended Pi LAN URL is **http://allsky-pi4.local:8008/**. Localhost remains
**http://127.0.0.1:8008/**. See [Pi setup](raspberry-pi.md) and
[LAN configuration and security](phase4-operation.md#lan-access).
There is no authentication in this trusted-home-LAN phase. Any trusted LAN user
can use Settings; use an authenticated TLS reverse proxy before broader access.

**Settings** saves a fixed list of validated toggles/thresholds to the actual TOML
file atomically, preserving comments, observer values and secret paths. It reports
**Saved — restart required**; stop with Ctrl+C and run `serve` again. Changes are
audited by key name only. Observer/network/source URLs, paths and credentials are
edited locally, never through the web form. Stop the service before CLI/manual
configuration maintenance. To add newly introduced defaults without erasing values:

```powershell
.venv\Scripts\python.exe -m all_sky_alert --config config.toml config-sync
```

Omitted new detector defaults remain disabled; synchronization does not silently
opt users into detectors. The explicit development enablement above is separate.
Filters use registry-derived checkboxes and URL state; All/None affects Upcoming
and Now, leaving history, storage and alert policies intact. The health stoplight
uses source-specific freshness and required/optional dependency roles: green is
sufficiently fresh, yellow is partial loss/fallback, red is a required capability
or central worker/database failure, gray is initialization.

**Live aircraft map:** self-hosted Leaflet 1.9.4, optional OpenStreetMap tiles, pan,
zoom, touch selection and observer/radius overlay. The browser polls our normalized
API every 15 seconds; it never downloads ADS-B feeds. Routine aircraft appear even
when no alert is warranted. Solid trails are observed points retained for ten
minutes (150 aircraft, 80 points each by default); dashed continuations are bounded
constant-motion estimates, not filed routes. Restart clears trails. Altitude source,
position age, range and heading are labeled. Routes/types use independently aged
licensed enrichment only; unknown routes remain unknown. Online tiles reveal the
viewed region to the tile provider; disable them in Settings for a blank base map.
See [tile policy and map bounds](phase4-operation.md#aircraft-map).

**Atmospheric optics:** 22° halos, sun dogs, circumzenithal/tangent arcs, low-Sun
pillars, lunar halos and separate cold/weak-wind/ice-saturation diamond-dust
conditions. These are coarse opportunity forecasts with crystal habit/orientation
unknown, not calibrated probabilities. Geometry, crystal suitability and low-cloud
obstruction are explained separately. Direct HRRR is optional and CONUS-only;
selected GRIB records are range-downloaded and cached per model run, never entire
continental files. The development config enables it after a successful Windows
decoder probe; the portable example keeps it opt-in. Install the `hrrr` extra if
needed, then enable `providers.hrrr.enabled`. Missing microphysics falls back to
shared coarse weather. Defaults select hybrid levels 20/30 at +3/+6 hours, a
32 MB/run budget, six-hourly runs and hourly cache checks. These sparse samples
cover only a small part of the 48-hour optics horizon; absence at sampled levels
does not establish absence throughout the atmosphere.

To evaluate once without starting the UI (stop the running service first):

```powershell
.venv\Scripts\python.exe -m all_sky_alert --config config.toml evaluate
```

This uses your enabled notification methods; with the supplied configuration it
only records local UI alerts. The OS lock prevents simultaneous coordinators on
the same data directory. Use **one worker**, without uvicorn `--reload`.


For production Pi installation, use [README.md](../README.md). Scientific module
configuration, Gmail operation and source troubleshooting are in
[prediction-guide.md](prediction-guide.md). Run tests with
`.venv\Scripts\python.exe -m pytest` on Windows.
