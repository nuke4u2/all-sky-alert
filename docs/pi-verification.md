# Raspberry Pi migration verification, 2026-10-11

Implementation and offline validation were performed on the existing **Windows /
Python 3.12** development workspace. Repository status was clean before editing.
No commits, pushes, additional agents, production config, secrets, OAuth files,
production SQLite, provider acquisition, live mail, Pi/systemd installation or
real camera access was used. An unrelated untracked `pi4sky_workspace.code-workspace`
appeared during work and was left untouched.

## Files and behavior

| Files | Change |
| --- | --- |
| `requirements.txt`, `constraints.txt` | Install project metadata with tested constraints; retain apt NumPy on Linux ARM64 |
| `config.example.toml`, `config.py`, `config_edit.py` | Camera schema/defaults/validation; fixed web operational allowlist |
| `camera/__init__.py`, `settings.py`, `backend.py`, `manager.py`, `timelapse.py` | Lazy mockable Picamera2 boundary, single owner, bounded demand-only preview, full-resolution captures, focus, schedule |
| `service.py`, `web.py`, `server.py`, `cli.py` | Explicit composition/lifespan, protected camera routes/status, early camera stop before HTTP drain, offline `check` command |
| `templates/base.html`, `settings.html`, `camera.html`, `static/camera.js`, `style.css` | Separate Camera navigation/page, supported focus controls, timelapse session/persist controls and responsive status |
| `deploy/systemd/all-sky-alert.service` | Reviewable unprivileged native systemd example; not installed/enabled |
| `scripts/pi_camera_diagnostic.py` | Manual apt-stack/full-resolution/autofocus hardware diagnostic |
| `scripts/pi_camera_browser_smoke.py`, `tests/test_camera.py` | Offline fake-hardware tests and real HTTP/browser integration |
| `README.md`, `HANDOFF.md`, `docs/architecture.md`, `phase4-architecture.md`, `phase4-operation.md` | Native Pi primary deployment; preserve prediction/security contracts; deprecate container operation |
| `docs/raspberry-pi.md`, `camera-architecture.md`, `pi-verification.md` | Exact install/diagnostic/foreground/systemd procedures, ownership/resource contracts and evidence |
| `Dockerfile` | Deprecation comment only; historical build recipe retained |

`pyproject.toml`, detector/provider/profile registries, scientific gates, domain
envelopes, alert identity/outbox, SQLite schema, Gmail implementation and network
trust policy were not changed. `.dockerignore` already excludes private/runtime
state and was retained. No new pip runtime dependency or Pi-only unconditional
import was added. Picamera2/libcamera and native image dependencies are apt-owned.

## Portability audit

Source/test/document/deployment inventory and platform-sensitive code paths were
reviewed, without traversing ignored private runtime/config/credential files.

| Area | Finding / disposition |
| --- | --- |
| Windows/container instructions | README made Pi-first; Synology recipe removed, container assets marked unmaintained. Older verification/history remains useful. |
| Paths and writable source assumptions | Core data/config paths already use `pathlib`, config-relative resolution. Document migration of Windows/Docker absolute paths. Camera paths resolve below configurable data storage, not installed package/source. Config parent must be writable for atomic replacement. |
| Locking | Existing Windows `msvcrt` / Linux `fcntl` OS locks retained; one process/worker remains required. Real Linux contention still needs target acceptance. |
| SQLite/threading | Existing local WAL, per-operation connections and busy timeout are portable. Camera does not share a capture loop with prediction workers; snapshot/queue locks are explicit. No multiprocessing dependency introduced. |
| Temp/config files | Existing closed temporary file + same-directory `os.replace` retained. Camera publishes only completed JPEGs; failed partials are removed. Power loss can leave unpublished `.part` files. |
| Timezones | Aware UTC and `zoneinfo` retained; capture filenames UTC, schedule monotonic. Trixie apt `tzdata` and existing Python dependency support presentation timezone data. |
| Signals and web streams | Infinite MJPEG can otherwise prevent HTTP drain/lifespan shutdown. Application server now stops camera before draining HTTP, with a bounded HTTP deadline. Backend release verified with fake hardware and open browser stream. Actual Linux SIGTERM remains pending. |
| ARM64/native dependencies | Runtime requirements derive from metadata; no Windows-only package or x86 wheel URL added. Existing NumPy pin would shadow apt NumPy: Linux ARM64 exception added. Picamera2/NumPy/Pillow/libcamera are distro-owned; Python 3.13/aarch64 pinned-wheel resolution is not claimed tested. |
| Optional HRRR | No decoder/source/scientific change; optional extra remains separate. Pi ARM64 ecCodes installation/decode remains unverified; keep disabled for initial acceptance. |
| LAN | Configurable port/bind, exact hosts, private peers, proxy policy and CSRF retained. Documented opt-in `allsky-pi4.local:8008`; no tunnel/public exposure/firewall change. Actual LAN routing/mDNS remains pending. |

## Measured checks

| Check | Result |
| --- | --- |
| Baseline full offline suite before changes | **229 passed**, 7 dependency deprecation warnings |
| Final targeted camera + settings suite | **52 passed**, 1 existing Starlette warning |
| Full offline suite after camera/timeout/check additions | **261 passed**, 7 existing warnings; no test skips or genuine failures |
| `python -m compileall -q src tests scripts` | Passed |
| `python -m pip check` in existing Windows venv | No broken requirements |
| Fixture-only real Uvicorn/Edge browser | Passed at 360, 390, 768, 1280 pixels; no horizontal overflow or page JavaScript errors |
| Browser operations | Preview and disconnect stop encoding; autofocus/manual controls; capture/download; invalid interval; minutes conversion; Start/Stop; persisted defaults and Settings visibility; hostile model text rendered as text |
| Open MJPEG during server shutdown | Passed; server thread ended and backend/owner released; refined shutdown path emitted no cancellation error trace |
| Real Camera Module 3 | **Not performed**: Windows workspace has no physical camera/Pi session |
| Linux ARM64/Python 3.13 install, systemd unit verification | **Not performed**: no target execution environment |
| Production credentials/network/provider evaluation | **Not performed**: offline fixtures only |

The first sandboxed pytest invocation encountered Windows temporary-directory
access restrictions (76 passed, 153 fixture setup errors), and a sandboxed
targeted run encountered the same class of temporary-directory access problem.
Approved runs outside that restriction passed without changing or weakening tests.
Those were environment errors, not Linux/ARM portability defects. Browser script
development also corrected an async Stop assertion race and a CSP-incompatible
test expression; final checks passed. No Pillow pip dependency was added for QA:
the browser canvas generates the synthetic JPEG fixture.

New unit coverage includes finite interval bounds, strict TOML parsing/defaults,
data path containment, UTC unique filenames, no catch-up scheduling, completion
pacing, session start/stop, one hardware owner, pending-capture/viewer bounds,
failed capture/focus isolation, unavailable camera, explicit timeout job flush,
native full-resolution configuration/restoration and offline CLI database check.
Mocked web integration covers no acquisition on reads, CSRF/origin/body-size/path
rejection, queued completion, download, persisted schedule/revision conflicts and
prediction worker continuity when camera startup fails. Browser integration uses
real HTTP/MJPEG and fake camera bytes; it does not prove sensor image quality.

Commands used locally:

```powershell
.venv\Scripts\python.exe -m pytest --tb=short
.venv\Scripts\python.exe -m pytest tests/test_camera.py tests/test_lan_settings.py --tb=short
.venv\Scripts\python.exe -m compileall -q src tests scripts
.venv\Scripts\python.exe -m pip check
$env:PYTHONPATH='src'; .venv\Scripts\python.exe scripts/pi_camera_browser_smoke.py
```

Browser report/screenshots are ignored QA artifacts under
`test-artifacts/pi-camera-browser/`. The `qa` extra and an installed browser are
required for this optional script. Pi clean-install, required apt packages, exact
camera diagnostics, first foreground command and reviewed systemd install commands
are all in [raspberry-pi.md](raspberry-pi.md).

## Remaining acceptance and future work

Before enabling production systemd, perform the complete target procedure in the
Pi guide: verify aarch64/system Python/dependencies, camera device `video` access,
actual autofocus/lens limits, full-resolution JPEG dimensions/quality, three or
more timed captures, prediction responsiveness, free space, LAN/mDNS and hardware
release under Ctrl+C and systemd SIGTERM. Review the rendered unit before installing.

Camera hotplug/reconnect, image retention/quotas, exposure automation and movie
assembly are deferred. Native driver hangs require process-level recovery; an
individual ordinary capture/focus error does not stop prediction workers. CPU/CMA
memory use and sustained SD-card throughput require measurements on the real Pi.

Future ZWO support should implement an explicitly registered backend with its own
capabilities, SDK licensing/ARM64 validation and device owner/resource budget.
Image-analysis results must cross separately reviewed scientific contracts if
ever used for predictions. No current camera frame is analyzed or reported as a
meteor, aurora, star solution or calibrated scientific observation.
