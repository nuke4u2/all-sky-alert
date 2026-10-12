# General Pi Camera / Timelapse contracts

This subsystem serves Camera Module 3 viewing/focus/stills/timelapse. It has no
detector registration, atmospheric dependency, Candidate, notification adapter,
scientific quality/probability or image interpretation. The prediction architecture
and SQLite schema remain intact. Native Pi deployment is documented in
[raspberry-pi.md](raspberry-pi.md); ZWO All-Sky Camera is deferred.

## Ownership and dependency direction

`service.py` explicitly constructs `CameraManager` (or accepts an injected manager).
Construction imports no Picamera2/libcamera, probes no hardware, and writes no
images. Web lifespan starts one manager after acquiring the coordinator lock;
shutdown stops new scheduling, waits for the owner and releases hardware before
closing the service. `evaluate`/`check` do not start cameras. One server process
remains mandatory; other `rpicam` programs must not run concurrently.

`server.py` stops the camera producer before Uvicorn drains HTTP connections,
allowing active infinite MJPEG responses to end and normal lifespan cleanup to
run. HTTP requests also have a ten-second graceful deadline as a fallback.
Prediction lane tasks then finish through the existing lifespan cleanup; they
are not camera stream tasks. Camera consumers must reconnect after restart.

`camera/backend.py` defines the small `CameraBackend` protocol and explicit
`RaspberryPiCameraBackend`. Only the camera thread calls open, metadata, preview,
focus, capture and close. Picamera2 may use its own internal event-loop threads;
there is still one application owner, no separate timelapse/preview process.
Backend factory, UTC clock and monotonic clock are injectable for offline tests.
New backends require explicit config registration/validation, not dynamic discovery.

`camera/manager.py` publishes copies of its locked status and immutable latest JPEG
bytes. HTTP status reads do not wait on hardware. Explicit capture/focus POSTs
enqueue work, return 202 and expose completion via bounded job status. At most
four queued commands, one pending manual capture and 32 recent job records are
retained. No automatic command retry occurs after ambiguous web delivery.

Preview uses small RGB JPEGs at 1–5 fps, 640×480 / 2 fps by default. At most four
viewers share the same encoded latest frame. No frame backlog, continuous
full-resolution encoding, transcoding stack or browser upstream connection exists.
Encoding stops without viewers; the camera remains open for focus/metadata and
stills. Preview frames are cleared on capture, error and last viewer departure.
The backend switches to full sensor resolution using one buffer, captures to disk,
and restores preview/focus controls in `finally` for ordinary capture failures.

## Capture and scheduling

`camera/timelapse.py` owns a simple monotonic schedule, normalized seconds and UTC
timestamp/unique-suffix filenames. Interval is finite 10–604800 seconds. First
capture waits one interval; future captures wait one interval after completion.
Late/missed frames are skipped. Manual success resets the running timelapse's
next deadline, avoiding an immediate back-to-back scheduled capture. Mode changes,
preview and captures are serial on the same thread. A selected/in-flight capture
may finish after Stop; future scheduling stops immediately. Reconfiguration starts
a new interval; no persisted image backlog or restart replay exists.

Directories are validated below `service.data_dir`, paths come only from local
config, and web inputs never select filenames/directories. The owner creates
UTC-named `.jpg.part` files, publishes completed nonempty images with `os.replace`,
and removes ordinary failed partials. A power loss can leave a `.part` file: it is
not a published JPEG and requires manual housekeeping. The latest-download route
serves only the manager's latest successful image, not a directory browser. Images
remain on disk; only bounded preview bytes/status are retained in application memory.
No automatic retention deletion or video assembly is included.

Individual autofocus/disk/capture failures are isolated and logged with safe error
classes; later captures may recover. Picamera2 jobs have 15-second waits. Timeout
explicitly flushes jobs, treats driver state as uncertain and releases the camera
instead of accumulating pending work. Operator fixes/restarts after hardware faults;
prediction workers continue. Kernel/native-driver hangs cannot be forcibly canceled
by Python: owner shutdown waits at most 35 seconds and reports failure, and systemd
provides a process-level stop deadline. Physical SIGTERM/release acceptance remains
a Pi hardware check, not a guarantee derived from fake-backend tests.

## Controls, persistence and security

Autofocus modes are filtered using libcamera's actual `AfMode` bounds; trigger and
manual lens limits are advertised only when present. Manual numeric input is
finite and within reported camera limits, in dioptres with approximate calibration.
Focus command acceptance is distinct from successful focusing: native `AfState`
metadata reports Idle/Scanning/Focused/Failed. Focus mode/position are session-only.

All camera actions use existing exact Host/LAN checks and CSP, bounded JSON,
exact same-origin/Fetch-Site and a signed matching CSRF cookie/header scoped to
`/camera`. Unknown actions/fields and arbitrary paths are rejected. JavaScript
renders metadata/errors/filenames via `textContent`. GET status/pages do not
initiate data acquisition or detector calculation; GET stream only subscribes to
background preview production, without making direct hardware calls.

Camera operational fields appear in Settings's fixed allowlist. Camera-page Save
startup defaults calls the same `ConfigEditor` for only timelapse enabled/interval,
validates the entire config, checks revision and atomically replaces TOML. Saved
config always requires restart. Explicit Start/Stop controls change only session
schedule, with no source/provider/core config hot reload. Paths/backend/index stay
local-only. Camera status is separate in `/api/health`, not an unrelated scientific
source veto in the aggregate prediction stoplight.

## Future boundary

A ZWO backend may later implement the protocol with independent advertised
capabilities. Pi AF/lens controls cannot be required of every backend. Each physical
device needs a single owner and reviewed memory/exposure/storage budget. SDK
licenses, ARM64 wheels/native binaries, richer capture provenance and any image
analysis bridge into prediction contracts require separate reviewed work. No ZWO
placeholder implementation, runtime LLM or automated orchestration is added.
