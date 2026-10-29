# Native Raspberry Pi deployment and General Pi Camera / Timelapse

The primary target is **allsky-pi4**, Raspberry Pi 4, Raspberry Pi OS Lite
64-bit (Debian 13/Trixie, aarch64), one unprivileged Python process in a venv,
managed by systemd. Synology/Docker deployment is deprecated and currently
unmaintained; the old Docker files and verification reports are historical.
Camera Module 3 is a general viewing/timelapse camera. **ZWO All-Sky Camera**,
image analysis, meteor/aurora detection, stacking and plate solving are deferred.

## Clean installation on allsky-pi4

The [README](../README.md) is the self-contained fresh-clone installation path.
Run over SSH as the normal account that will own the service. The Pi checkout is
`~/projects/all-sky-alert`; do not copy a Windows venv, production config, secrets
or live data into a source transfer. These commands assume Raspberry Pi OS's
configured Raspberry Pi package repositories, not generic Debian alone.

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y --no-install-recommends \
  python3 python3-venv python3-pip git ca-certificates tzdata curl avahi-daemon \
  rpicam-apps-lite python3-picamera2 python3-libcamera python3-numpy python3-pil
sudo systemctl enable --now avahi-daemon
sudo usermod -aG video "$(id -un)"
```

Reboot after kernel/firmware updates and reconnect SSH so new group membership
takes effect. Check `hostname` is `allsky-pi4`, `uname -m` is `aarch64`, and
`python3 --version` is at least 3.12 (Trixie's system Python is 3.13).

```bash
cd ~/projects/all-sky-alert
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python -m pip check
which python
python --version
python -c "import all_sky_alert; print('All Sky Alert OK')"
python -c "from picamera2 import Picamera2; print('Picamera2 OK')"
python -c "import astronomy, fastapi, skyfield, sgp4, tomlkit, picamera2, libcamera, numpy; print('Imports OK; NumPy:', numpy.__version__, numpy.__file__)"
```

The [Raspberry Pi camera documentation](https://www.raspberrypi.com/documentation/computers/camera_software.html)
identifies `rpicam-apps-lite` as the headless package and supports apt Picamera2
without desktop recommendations. Apt resolves the camera native dependencies
(including JPEG/Pillow/PyAV and matched libcamera libraries); no Qt desktop,
OpenCV or FFmpeg application is required by this feature. The
[Picamera2 project](https://github.com/raspberrypi/picamera2#installation)
recommends apt to match the camera bindings. Use the system Python with
`--system-site-packages`, as recommended by the
[Picamera2 venv guidance](https://forums.raspberrypi.com/viewtopic.php?t=361758).
Do not install Picamera2/libcamera using pip, use `sudo pip`, or use a separately
compiled Python for this venv.

`requirements.txt` installs `.` with `-c constraints.txt`. Thus `pyproject.toml`
owns runtime requirements/extras and package data; `constraints.txt` supplies
tested versions only for dependencies selected by the installer. There is no
second handwritten runtime dependency list to drift. Dev/QA/Gmail/HRRR are not
installed by the runtime requirements. NumPy is declared directly because orbit
code imports it; the camera's imports remain lazy. Windows development remains
supported separately in [its guide](windows-development.md).

Linux ARM64 deliberately omits the existing NumPy 2.5.3 pin, allowing apt's NumPy
to remain visible rather than shadowing a camera dependency with a pip wheel.
[Debian Trixie packages NumPy 2.2.4](https://packages.debian.org/trixie/python3-numpy).
Do not use pip's `--upgrade`/`--ignore-installed` on the apt camera dependencies.
The apt stack follows distribution updates instead of a Python-only lock; record
both snapshots after successful hardware acceptance:

```bash
mkdir -p test-artifacts/pi-install
dpkg-query -W python3 python3-picamera2 python3-libcamera python3-numpy python3-pil rpicam-apps-lite > test-artifacts/pi-install/apt-versions.txt
python -m pip freeze > test-artifacts/pi-install/python-versions.txt
```

Optional packages, only when configured:

```bash
# Optional Gmail; does not authorize or send mail.
python -m pip install -c constraints.txt '.[gmail]'
# Optional offline tests.
python -m pip install -c constraints.txt '.[dev]'
# Optional sparse HRRR decoder; validate ARM64 decoder availability separately.
python -m pip install -c constraints.txt '.[hrrr]'
```

Keep HRRR disabled for the initial Pi acceptance. Core packages are portable
Python or published native-package dependencies; the 2026-10-12 Windows re-audit
checked the core constrained graph's Python 3.13/aarch64 wheel metadata, but did
not execute a native Pi install. No x86
wheel URL or Windows-only requirement is introduced. ARM64 pip/wheel resolution
and optional ecCodes remain target-machine acceptance checks; do not silently
relax scientific dependencies if a build fails.

## Camera diagnostics before running the service

Stop All Sky Alert and any other camera consumer first. Run as the service's
unprivileged user; running successfully as root would conceal permission issues.

```bash
id
ls -l /dev/video* /dev/media* /dev/v4l-subdev* /dev/dma_heap/*
rpicam-hello --list-cameras
mkdir -p test-artifacts/pi-camera
rpicam-still --nopreview --timeout 2000 --autofocus-mode auto -o test-artifacts/pi-camera/rpicam-test.jpg
.venv/bin/python scripts/pi_camera_diagnostic.py
ls -lh test-artifacts/pi-camera/*.jpg
```

Module 3 normally identifies as `imx708` (variant names can differ). The script
prints model, full sensor size, actual autofocus/lens control limits, autofocus
completion and image size, and releases the device in `finally`. Inspect the
actual image on another LAN machine. A file alone does not prove useful focus.
These outputs are ignored runtime QA artifacts, never source assets.

The Pi 4 camera uses media/video/subdevice and DMA heap nodes. Raspberry Pi OS
assigns camera devices to `video`; the systemd unit supplies that supplementary
group. Check ownership on the **actual updated Trixie image** with the `ls`
command above. If nodes/permissions are missing, repair the OS camera installation
and check its shipped udev rules; do not run the service as root or chmod devices
world-writable. `render` is not needed by this implementation because it opens no
DRM/display preview. The
[Raspberry Pi V4L2 documentation](https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/camera/v4l2.adoc)
describes the Pi camera device roles. Device/group verification on allsky-pi4 is
still required; no physical device permissions were inspected in this session.

## Configuration and migration of private runtime state

For a new installation only, create the config without overwriting an existing one:

```bash
test -e config.toml || cp config.example.toml config.toml
chmod 600 config.toml
nano config.toml
```

Set the real observer location/elevation/IANA timezone; the example is Greenwich.
For the LAN, edit the existing `[service]` table (do not append a duplicate):

```toml
data_dir = "data"
bind_host = "0.0.0.0"
port = 8008
lan_access = true
allowed_hosts = ["localhost", "127.0.0.1", "::1", "allsky-pi4", "allsky-pi4.local"]
trusted_proxy_ips = []
```

Add the Pi's actual private IP to `allowed_hosts` only if clients will use that
IP in their URL. Wildcard hosts/proxies remain forbidden. LAN peer checks and
exact Host checks are unchanged; forwarding is ignored by default. The intended
URL is **http://allsky-pi4.local:8008/**. Avahi supplies mDNS; clients must support
`.local` resolution. No router port forwarding, public exposure or tunnel is
part of this deployment. The existing UI has no account authentication and is
for a trusted LAN; its camera controls share that access model.

After diagnostics, set `[camera].enabled = true`. Camera directories resolve
**under `service.data_dir`**, so `capture_directory = "camera/stills"` and
`[camera.timelapse].directory = "camera/timelapse"` create `data/camera/...`.
They must stay below the data directory, are not web-editable, and remain ignored.
Preview defaults are 640×480 at 2 fps, with a maximum of 5 fps and four viewers.
Leave `[camera.timelapse].enabled = false` for initial manual verification.

For an existing installation, stop both old and new coordinators before copying:

- `config.toml`, preserving observer, notification thresholds and Gmail routing.
  Replace Windows absolute paths or Docker `/data`/`/secrets` paths with Pi paths;
  no automatic conversion is attempted.
- Configured Gmail desktop OAuth client file and token, only if Gmail is used.
  Put them in `secrets/`, restrict directory/file access (700/600), and install
  the Gmail extra. No OAuth file has been inspected or changed by this work.
- `data/all-sky-alert.sqlite3`, only to preserve prediction history, milestone
  deduplication, provider backoff/HTTP stops, cached slow geometry and aircraft
  episodes. Use SQLite backup or copy the full stopped database state, including
  any remaining `-wal`/`-shm` companions; never copy only a live WAL main file.
  Without history, old alert milestone knowledge is lost and alerts can recur.
- Any explicitly configured licensed enrichment JSON file, if used; its path,
  licensing and aging requirements still apply.
- Camera images, if desired, belong in runtime storage. No camera schedule sidecar
  exists: startup schedule is TOML; current jobs/preview/session state is transient.

Do not transfer `service.lock` or the Windows venv. Caches and HTTP stop state
are in SQLite, not a separate directory requiring transfer. Ensure files belong
to the service account and the project/config parent directory permits atomic
config replacement. Source installation itself need not be writable at runtime;
the config parent and data/capture directories do. For now these share the
project layout as requested. Use local storage, not a shared SMB/NFS database.

When upgrading a copied old config, with the service stopped:

```bash
.venv/bin/python -m all_sky_alert --config config.toml config-sync
```

Synchronization preserves supplied values and adds safe camera-disabled defaults.
Settings exposes camera enablement, preview size/rate and startup timelapse
enablement/interval. **Every saved config change still requires restart.** On the
Camera page, Start/Stop/interval changes affect the current session immediately;
Save startup defaults persists only the two timelapse fields with the existing
validation, bounded JSON, exact-origin CSRF, revision guard and atomic editor.
It does not reload the core config. Focus is session-only and uses controls
advertised by the installed camera; unsupported controls are disabled visibly.
Trigger autofocus selects Auto mode; its completion is reported via native
libcamera AF state, independently of acceptance of the control command.

## First foreground run and acceptance

Keep Gmail disabled during initial acceptance; normal `serve`/`evaluate` honor
enabled delivery settings. The new `check` command performs no fetch or mail:

```bash
cd ~/projects/all-sky-alert
.venv/bin/python -m pip check
.venv/bin/python -m all_sky_alert --config config.toml check
.venv/bin/python -m all_sky_alert --config config.toml serve
```

From a second SSH shell and a LAN client:

```bash
curl --fail http://127.0.0.1:8008/api/health
curl --fail http://allsky-pi4.local:8008/
curl --fail http://allsky-pi4.local:8008/camera/status
```

`check` validates the full config, initializes the normal components/offline
ephemeris and writes a SQLite health row under the coordinator lock. It does not
prove provider connectivity. During `serve`, verify the dashboard's source
health and slow/live/delivery worker rows become current. Existing required
sources may fail or be outside geographic coverage; inspect their timestamps,
errors and backoff instead of assuming HTTP success means scientific freshness.
Do not clear a persisted CelesTrak HTTP stop automatically.

In **Camera**:

1. Verify detected/model, actual full sensor resolution, no camera error.
2. Show live view, then hide it. View status and confirm preview stops encoding
   when hidden. No desktop window should appear.
3. Select Auto and Trigger autofocus; observe Scanning then Focused/Failed.
   Test Continuous and Manual within the reported lens limits; unsupported modes
   must remain unavailable. Inspect image focus visually.
4. Capture Image, confirm completion/time/filename, download the latest image,
   and check its dimensions equal the reported full sensor size.
5. Set 10 seconds, Start timelapse. Observe at least three full-resolution images
   in `data/camera/timelapse`. Start timestamps will be at least 10 seconds apart,
   usually longer because this schedule waits from capture completion.
6. Exercise minutes/hours input, invalid/too-short interval, Stop timelapse and
   Save startup defaults. Stop cancels future scheduling; one already-selected
   capture may finish. No full-service restart is needed for session controls.
7. Ensure predictions, health and other web requests remain responsive during a
   capture. Preview intentionally pauses for the full-resolution mode switch.
8. Stop with Ctrl+C, then rerun `rpicam-hello --list-cameras` and the diagnostic
   script to confirm camera ownership was released. Repeat using `systemctl stop`
   after systemd installation to verify SIGTERM behavior.

Individual disk/focus/capture errors are reported without stopping prediction
workers; subsequent scheduled captures can succeed. A sensor operation timeout
clears pending Picamera2 jobs, stops the camera worker and releases the device:
restart the service after fixing hardware, rather than issuing work against an
uncertain driver state. The core service remains available. No automatic camera
hotplug/reconnect or retention deletion is implemented. Monitor free disk space
and plan storage/backup manually; large JPEG timelapses can exhaust an SD card.

## Review and install systemd

The [installer](../scripts/install_systemd.py) renders
[deploy/systemd/all-sky-alert.service](../deploy/systemd/all-sky-alert.service)
using its checkout path and the current unprivileged SSH account. The unit uses
the project venv/working directory, `video`, journal logs,
10-second failure restart delay, private temporary storage, restrictive umask,
SIGTERM and a 300-second graceful-stop allowance for prediction calculations.
It keeps real camera devices and home/project access available.
The application stops the camera producer before Uvicorn drains HTTP/MJPEG
connections, so open live views end before camera/worker lifespan cleanup.
A ten-second HTTP graceful deadline remains as a fallback.

After foreground acceptance, stop the foreground server and run as the normal
service account, **without a leading sudo**. Review the dry-run output, then install:

```bash
cd ~/projects/all-sky-alert
.venv/bin/python scripts/install_systemd.py --dry-run
.venv/bin/python scripts/install_systemd.py
```

The script checks prerequisites, validates the rendered unit with
`systemd-analyze verify`, installs it as root-owned mode 644, reloads systemd,
enables startup at boot, and starts/restarts it immediately. Only installation
and privileged systemctl commands use sudo. Re-running replaces the same unit
and restarts it. Dependencies and config must already be installed; the script
does not edit config, run config-sync or migrate private state. Normal service
startup honors configured providers, camera and notification delivery.

Options: `--no-start` installs/enables boot startup without a start/restart now;
it does not stop an already running service. `--config /path/to/config.toml`
selects a different config; relative paths resolve from the checkout. `--dry-run`
checks prerequisites and prints the rendered unit without writes or commands.

Inspect operation and confirm a real boot when convenient:

```bash
systemctl is-enabled all-sky-alert
systemctl status all-sky-alert --no-pager
journalctl -u all-sky-alert -f
# Ctrl+C exits the log viewer. Reboot, reconnect, then check status again:
sudo reboot
systemctl status all-sky-alert --no-pager
```

To remove boot startup and stop now, run
`sudo systemctl disable --now all-sky-alert`. Stop before manual/CLI config
maintenance. After a web config save:

```bash
sudo systemctl restart all-sky-alert
```

For shutdown/release acceptance:

```bash
sudo systemctl stop all-sky-alert
journalctl -u all-sky-alert -n 100 --no-pager
.venv/bin/python scripts/pi_camera_diagnostic.py
sudo systemctl start all-sky-alert
```

## Offline tests and validation limits

On the Pi, install the dev extra and run:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m pytest tests/test_camera.py tests/test_lan_settings.py
.venv/bin/python -m compileall -q src tests scripts/pi_camera_diagnostic.py
```

Network is blocked by normal tests. Camera tests inject fake hardware; they cover
configuration, interval bounds, UTC unique filenames, monotonic schedule/missed
frames, owner thread, preview bounds, start/stop, failed operations, resource
release, full-resolution mode restoration and web security/persistence. Mocked
web/backend integration is distinct from real Camera Module 3 hardware acceptance.
See [current re-audit](pi-reaudit.md) for the measured local results and the
explicit outstanding ARM64, hardware, browser and systemd checks.

Future ZWO work should implement the small `CameraBackend` boundary and receive
its own explicit config/ownership/resource budget. Pi focus capabilities must
not become a universal camera requirement. A second physical camera can have a
separate manager; two consumers of the same device must share its owner. Capture
metadata, ZWO SDK licensing/ARM64 support, long exposures, calibration and any
future image-analysis-to-Candidate bridge need their own reviewed contracts.
No ZWO implementation or scientific image inference exists in this change.
