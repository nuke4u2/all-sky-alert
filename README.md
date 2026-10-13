# All Sky Alert

All Sky Alert predicts local sky opportunities, records their revisions and alert
milestones in SQLite, and serves a small LAN web UI. The primary deployment is a
**Raspberry Pi 4 named `allsky-pi4`, Raspberry Pi OS Lite 64-bit, Debian 13/Trixie,
aarch64, headless, system Python venv and systemd**. Windows is a development
environment. Docker/Synology deployment is deprecated and not currently maintained.

**Verification status:** the current audit ran on Windows, not the Pi. A clean
local runtime install, offline tests and dependency wheel metadata are evidence
for parts of the setup; native Pi installation, camera hardware and Linux SIGTERM
acceptance remain pending. See [audit evidence](docs/pi-reaudit.md). Do not treat
the previous camera implementation or these instructions as hardware acceptance.

## Features

- Jupiter/Saturn/Mars observing windows, sunset opportunities and observer-local
  solar/lunar eclipses with horizon clipping and solar safety.
- NOAA OVATION aurora opportunities, ISS/configured spacecraft passes, experimental
  ISS Sun/Moon center-track transits and live ADS-B overhead episodes/map.
- Shared weather, optional native Canadian seeing categories and optional sparse
  HRRR ice-crystal optics opportunities. Scores are uncalibrated opportunities;
  unknown inputs never mean clear skies, and stale scientific inputs cannot alert.
- Durable predictions/history, unique alert milestones, optional Gmail, source
  health and validated Settings. Routine web reads acquire no upstream data.
- **General Pi Camera / Timelapse:** separate Camera page, low-rate live view,
  supported autofocus/manual lens controls, full-resolution JPEG capture and
  configurable timelapse. Camera Module 3 is separate from the future **ZWO
  All-Sky Camera**; no ZWO implementation or image analysis exists.

Detailed scientific methods, limits, configuration and optional email operation
remain in the [prediction guide](docs/prediction-guide.md),
[architecture](docs/architecture.md) and [source matrix](docs/source-capabilities.md).

## Raspberry Pi deployment — primary

Use Raspberry Pi OS Lite **64-bit Trixie**, with SSH enabled and the Raspberry Pi
apt repositories configured. Run as the normal unprivileged account, not root.
Trixie supplies Python 3.13; the application requires Python 3.12 or newer.

```bash
hostname
uname -m
cat /etc/os-release
python3 --version
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y --no-install-recommends \
  git ca-certificates tzdata curl avahi-daemon \
  python3 python3-venv python3-pip \
  rpicam-apps-lite python3-picamera2 python3-libcamera python3-numpy python3-pil
sudo usermod -aG video "$(id -un)"
```

Reboot if kernel/firmware was updated, then reconnect SSH to refresh group
membership. Confirm the hostname is `allsky-pi4` and architecture is `aarch64`.
Avahi provides `.local` naming; if it is not running, start it with
`sudo systemctl enable --now avahi-daemon`.

For a fresh clone:

```bash
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/nuke4u2/all-sky-alert.git
cd ~/projects/all-sky-alert
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
which python
python --version
python -m pip check
python -c "import all_sky_alert; print('All Sky Alert OK:', all_sky_alert.__file__)"
python -c "from picamera2 import Picamera2; print('Picamera2 OK')"
python -c "import libcamera, numpy; print('NumPy:', numpy.__version__, numpy.__file__)"
```

The repository is already cloned on your Pi: skip `git clone` there. Inspect an
existing `.venv/pyvenv.cfg` before changing it: it should reference the Pi's system
Python and have `include-system-site-packages = true`. A Windows venv cannot be
reused. If a stopped installation has the wrong venv, preserve it first with
`mv .venv .venv.before-pi-audit` (choose an unused backup name), then recreate it
using the commands above. Do not replace a running service's environment.

**Why `--system-site-packages`:** apt installs Raspberry Pi's matched Picamera2 /
libcamera Python bindings and native dependencies in the system interpreter's
package directories. The venv needs visibility of those packages. Use `python3`
from the OS, not a separately compiled Python. Do not pip-install Picamera2 or
libcamera, and never use `sudo pip`. The headless camera package choice follows
[Raspberry Pi's camera documentation](https://www.raspberrypi.com/documentation/computers/camera_software.html)
and [Picamera2 installation guidance](https://github.com/raspberrypi/picamera2#installation).

**Dependency ownership:** `pyproject.toml` declares Python runtime requirements;
`constraints.txt` records tested versions; `requirements.txt` contains
`-c constraints.txt` and `.` to install those declarations without a duplicated
package list. Constraints do not install optional extras. NumPy is a direct
requirement because runtime orbit code imports it; on Linux ARM64 its Windows
snapshot pin is omitted so the apt camera-compatible NumPy remains visible.
The apt stack is versioned by the OS, so record `dpkg-query -W` and `pip freeze`
after successful Pi acceptance. The [dependency audit](docs/pi-reaudit.md#runtime-dependencies)
lists every direct requirement, imports, native prerequisites and wheel evidence.

## Configuration

The application creates its database/data directory when initialized and capture
subdirectories when an image is saved. Paths resolve from the configuration file;
camera directories resolve below `service.data_dir`. No username is embedded in
application source. The default project layout works at
`/home/wnicoll/projects/all-sky-alert` as its normal owner.

For a **new installation only**, create a config without replacing an existing one:

```bash
test -e config.toml || cp config.example.toml config.toml
chmod 600 config.toml
nano config.toml
```

Set your real `[observer]` latitude/longitude/elevation/IANA timezone; the example
coordinates are Greenwich. Keep Gmail and optional HRRR disabled for first-run
acceptance. After camera diagnostics pass, set `[camera].enabled = true`.

For LAN operation, change these values in the existing `[service]` table:

```toml
data_dir = "data"
bind_host = "0.0.0.0"
port = 8008
lan_access = true
allowed_hosts = ["localhost", "127.0.0.1", "::1", "allsky-pi4", "allsky-pi4.local"]
trusted_proxy_ips = []
```

Add the Pi's actual private IP to `allowed_hosts` if you intend to browse by IP.
Do not append a duplicate TOML table. Unknown settings are rejected. Settings
offers a fixed operational allowlist; saved config requires restart. Stop the
service before manual/CLI config maintenance. `config-sync` adds missing defaults
while preserving existing values; omitted new detectors/camera stay disabled.

Private files remain outside Git: `config.toml`, `secrets/`, tokens/client
credentials, `data/`, SQLite/WAL files, licensed enrichment and captures. For a
migration, copy the configured private files separately and only as needed.
Preserve SQLite history to retain alert deduplication: use SQLite backup or copy
the complete stopped database state, never just a live WAL main file. Replace
Windows drive paths or Docker `/data`/`/secrets` paths in the copied config.
See [migration details](docs/raspberry-pi.md#configuration-and-migration-of-private-runtime-state).

## Running manually

Stop any existing coordinator first. `check` validates config, initializes local
components and verifies SQLite writes; it does not fetch providers, send mail or
open a camera. It uses the configured database, so it is an operator command,
not a way for a bounded audit to inspect production data.

```bash
cd ~/projects/all-sky-alert
.venv/bin/python -m all_sky_alert --config config.toml check
.venv/bin/python -m all_sky_alert --config config.toml serve
```

From another shell:

```bash
curl --fail http://127.0.0.1:8008/api/health
curl --fail http://allsky-pi4.local:8008/
curl --fail http://allsky-pi4.local:8008/camera/status
```

Inspect source health and slow/live/delivery worker timestamps in the dashboard.
Initialization is not proof of upstream connectivity/freshness. Keep one process
and one worker; do not use `--reload`. Ctrl+C stops the foreground service.
`evaluate` honors configured notification methods; it can send mail if enabled.

## Camera Module 3

Stop All Sky Alert and other camera consumers before these diagnostics:

```bash
id
rpicam-hello --list-cameras
mkdir -p test-artifacts/pi-camera
rpicam-still --nopreview --timeout 2000 --autofocus-mode auto \
  -o test-artifacts/pi-camera/rpicam-test.jpg
.venv/bin/python scripts/pi_camera_diagnostic.py
ls -lh test-artifacts/pi-camera/*.jpg
```

Run as the service account. Module 3 reports an IMX708 variant. If permissions
fail, inspect `ls -l /dev/video* /dev/media* /dev/v4l-subdev* /dev/dma_heap/*`
and the OS camera installation; do not run the app as root. The unit supplies
the `video` group. Exact node/group permissions still need checking on your Pi.
This implementation needs no desktop/DRM preview or `render` group.

Once enabled, **Camera** provides a 640×480 / 2 fps demand-only preview, actual
focus capabilities/lens limits, full sensor resolution captures, and timelapse
intervals in seconds/minutes/hours. Interval bounds are 10 seconds to 7 days.
One owner serializes preview, focus and captures; there are no competing camera
processes. Preview pauses for full-resolution capture and stops encoding when
hidden. Default outputs are ignored `data/camera/stills/` and
`data/camera/timelapse/`.

Start/Stop changes the current session immediately. Save startup defaults uses
the existing validated atomic TOML editor and takes effect after restart. Watch
autofocus state for actual focus completion, inspect captured image dimensions
and focus, and verify at least three timed captures followed by Stop. Ordinary
failed captures/focus commands do not stop prediction workers; a driver timeout
releases the camera and requires operator repair/restart. Monitor disk capacity;
automatic retention, movie assembly, exposure automation and hotplug recovery
are not implemented. [Detailed acceptance](docs/raspberry-pi.md#first-foreground-run-and-acceptance)
and [camera contracts](docs/camera-architecture.md) distinguish these features
from deferred ZWO imaging and scientific image analysis.

## systemd service

First pass foreground/camera acceptance, stop the foreground server, and review
the rendered unit. The distributable template contains `ALL_SKY_USER`; substitute
the normal SSH/service account, never root. Adjust paths if the project is elsewhere.

```bash
cd ~/projects/all-sky-alert
mkdir -p test-artifacts/pi-install
sed "s/ALL_SKY_USER/$(id -un)/g" deploy/systemd/all-sky-alert.service \
  > test-artifacts/pi-install/all-sky-alert.service
cat test-artifacts/pi-install/all-sky-alert.service
systemd-analyze verify test-artifacts/pi-install/all-sky-alert.service
```

After review, install manually:

```bash
sudo cp test-artifacts/pi-install/all-sky-alert.service /etc/systemd/system/all-sky-alert.service
sudo systemctl daemon-reload
sudo systemctl enable --now all-sky-alert
systemctl status all-sky-alert --no-pager
journalctl -u all-sky-alert -f
```

The unit uses the project venv/working directory, networking order, `video`,
unprivileged execution, restrictive umask, journald, failure restart/backoff and
SIGTERM. It does not embed secrets. The server ends camera streams before draining
HTTP and the lifespan waits for prediction workers; systemd supplies the final
stop deadline. Verify hardware release with `sudo systemctl stop all-sky-alert`
followed by the diagnostic script before restarting. The audit does not install,
enable or start this unit automatically.

## LAN access

Expected URL after configuration: **http://allsky-pi4.local:8008/**. Loopback is
**http://127.0.0.1:8008/**. Binding is configurable; defaults remain loopback until
you opt into LAN access. Exact Host, private-peer, explicit-proxy and same-origin
CSRF controls remain enforced. There is no account authentication; use only a
trusted LAN. No public exposure, router forwarding, UPnP, tunnel or Raspberry Pi
Connect service is needed. Optional map tiles go to their configured public tile
provider; disable them in Settings if desired.

## Testing and troubleshooting

Optional tests need only the dev extra, not browser/container packages:

```bash
source .venv/bin/activate
python -m pip install -c constraints.txt '.[dev]'
python -m pytest
python -m pytest tests/test_camera.py tests/test_lan_settings.py
python -m compileall -q src tests scripts
python -m pip check
```

Tests block network and mock camera hardware. Optional HRRR decoder tests skip
when ecCodes is absent; browser QA requires a separately installed browser/qa
extra and is not needed for ordinary deployment. Real sensor and SIGTERM tests
are separate. For installed-package acceptance with private config/database and
disabled upstream/mail, without reading live files:

```bash
.venv/bin/python scripts/pi_native_smoke.py --require-installed
# Explicit real hardware test: stop All Sky Alert and other camera consumers first.
.venv/bin/python scripts/pi_native_smoke.py --require-installed --camera
```

- **Picamera2 import fails:** verify the system interpreter, apt package and
  `.venv/pyvenv.cfg` system-site setting. Do not repair it with pip Picamera2.
- **Install conflict or binary import failure:** run `pip check`, confirm NumPy
  resolves from apt on the Pi, and record apt/pip versions. Do not copy Windows
  wheels or silently replace the camera's native dependency stack.
- **Cannot save Settings/SQLite/captures:** check the service account owns the
  data directories and can replace files in the config parent directory. Use
  local storage; do not solve permissions by running as root.
- **HTTP 400:** allowlist the exact hostname/IP used in the URL. **403:** inspect
  the private-peer/origin checks. **Connection refused:** check bind/port/service
  logs. If `.local` fails, verify Avahi or use an explicitly allowlisted LAN IP.
- **Camera busy:** stop other consumers and the existing service before diagnostics.
  **No camera:** check cabling and `rpicam-hello --list-cameras` before Python.
- **Provider unhealthy:** examine source timestamps/backoff; a fresh HTTP response
  does not make old scientific data current. Never automatically reset the
  persisted CelesTrak non-200 HTTP stop.

## Development and updates

For a stopped native deployment:

```bash
sudo systemctl stop all-sky-alert   # omit if using a stopped foreground server
cd ~/projects/all-sky-alert
git status
git pull --ff-only
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip check
python -m all_sky_alert --config config.toml check
sudo systemctl start all-sky-alert  # or restart the foreground serve command
systemctl status all-sky-alert --no-pager
```

Preserve private config/secrets/history, stop before maintenance, and back up the
database before upgrades. Do not run `config-sync` automatically against live
config. Windows PowerShell setup/testing is preserved in
[Windows development only](docs/windows-development.md). The
[handoff](HANDOFF.md) and [development contracts](AGENTS.md) govern local changes;
no runtime LLM or agent orchestration dependency is required.

## Deprecated / legacy deployment

Docker/Synology is **deprecated / not currently maintained**. Historical
`Dockerfile`, `.dockerignore`, container scripts and phase verification reports
are retained for reference, not recommended installation instructions. Prior
Windows/container checks do not establish Pi hardware compatibility.
