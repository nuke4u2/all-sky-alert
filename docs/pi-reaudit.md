# Fresh native deployment audit — 2026-10-12

**Migration acceptance is pending.** The request described a Pi workspace, but
the actual tool host was `Weissbier`, Microsoft Windows 10.0.26200, Python 3.12.8
AMD64, repository `E:\AI\AgentProjects\all-sky-alert`. The user subsequently
instructed: **complete the local audit; leave Pi verification pending**. No SSH,
apt action, Pi installation, camera hardware access or systemd enable/start was
performed. Local Windows success and published ARM64 wheels are distinct evidence.

The authoritative target remains allsky-pi4, Pi 4, Raspberry Pi OS Lite 64-bit,
Debian 13/Trixie, aarch64, headless venv/systemd, LAN-only, Camera Module 3. The
[README](../README.md) is now the complete primary installation guide; detailed
camera/migration procedures remain in [raspberry-pi.md](raspberry-pi.md).

## Initial repository and environment

Exact initial `git status`:

```text
On branch main
Your branch is up to date with 'origin/main'.

nothing to commit, working tree clean
```

The existing `.venv/pyvenv.cfg` identifies Windows Python 3.12.8 with
`include-system-site-packages = false`. It was inspected and preserved. A new
isolated non-editable install venv was created only in ignored
`test-artifacts/pi-reaudit/install-venv/`; it is Windows and must not be copied
to the Pi. Production `config.toml`, secrets, tokens, credentials, `data/`, live
SQLite and captures were not read or modified. No commit was created.

## Repository inspection and search

The tracked inventory contained 116 files. README, HANDOFF, AGENTS, project /
requirements / constraints metadata, example config, Docker/ignore files,
deployment template, runtime modules, camera subsystem, tests/fixtures, scripts
and documentation were inspected. Searches used explicit repository paths and
covered documentation and scripts as well as source; production/ignored private
files, venv internals, generated artifacts and binary fixtures were excluded.
Vendored Leaflet minified code/licenses were retained.

Representative executed searches:

```bash
rg -n -i 'Windows|PowerShell|python.exe|localhost|Docker|container|Synology' \
  README.md AGENTS.md HANDOFF.md docs scripts src tests deploy \
  pyproject.toml requirements.txt constraints.txt config.example.toml Dockerfile \
  -g '!leaflet.js' -g '!leaflet.css' -g '!__pycache__/**'
rg -n -F -e '.venv\Scripts' -e 'C:\' -e 'E:\' -e 'win32' -e 'os.name' \
  -e 'platform.system' -e 'subprocess' -e 'multiprocessing' -e 'signal.' \
  src tests scripts docs README.md HANDOFF.md AGENTS.md deploy \
  -g '*.py' -g '*.md' -g '*.service'
rg -n '^(from|import) ' src/all_sky_alert -g '*.py'
```

Search command syntax was passed through PowerShell locally; the examples above
show the equivalent Linux invocation. Literal `Windows` also matches ordinary
astronomical "windows" case-insensitively; those are not platform assumptions.
An initial broad hidden/glob invocation returned no matches, so the audit reran
the searches against explicit source/doc/test/deployment paths, producing the
documented hits rather than treating the empty result as proof of portability.

## Runtime dependencies

This is the **complete direct runtime list from `pyproject.toml` after repair**.
All require the normal supported CPython interpreter, not x86-specific wheel URLs.
PyPI metadata was fetched without credentials and checked against CPython 3.13 /
Linux aarch64 wheel tags, active Linux markers and the constrained dependency
graph. This is a metadata preflight, not execution or an actual Pi pip resolver run.

| Declaration | Constrained version | Runtime use / import evidence | Pi ownership and native prerequisites | Published target artifact |
| --- | --- | --- | --- | --- |
| `astronomy-engine>=2.1.19,<3` | 2.1.19 | `astronomy.py` imports `astronomy` for local Sun/planet/eclipse geometry | pip, pure Python, no compiler/native library required | `py3-none-any` |
| `fastapi>=0.115,<1` | 0.142.2 | `web.py` imports FastAPI/request/response/static/template classes | pip; framework is pure Python, Pydantic's native core has an ARM64 wheel | `py3-none-any` |
| `uvicorn>=0.30,<1` | 0.54.0 | CLI/server run one ASGI process and handle shutdown | pip; default asyncio/h11 configuration, no uvloop/httptools build required | `py3-none-any` |
| `jinja2>=3.1,<4` | 3.1.6 | FastAPI/Starlette Jinja2Templates render packaged HTML | pip; MarkupSafe native wheel supplies its extension | `py3-none-any` |
| `httpx>=0.27,<1` | 0.28.1 | Service/providers/notification transport import HTTPX | pip, pure Python plus bundled certifi trust data and OS Python SSL | `py3-none-any` |
| `tzdata>=2025.1` | 2026.5 | `zoneinfo.ZoneInfo` in config/web/domain loads OS TZ data first and imports tzdata as a fallback; no direct app `import tzdata` | pip portable fallback retained for Windows; apt `tzdata` supplies OS timezone rules on Pi | `py2.py3-none-any` |
| `skyfield>=1.55,<2` | 1.55 | `orbits.py` imports EarthSatellite/load/load_file/wgs84 | pip; consumes NumPy/sgp4/jplephem | `py3-none-any` |
| `sgp4>=2.27,<3` | 2.27 | Skyfield EarthSatellite propagation and application version/provenance use SGP4; app need not import its extension directly | pip, native extension supplied by compatible ABI3 ARM64 wheel; no C++ toolchain needed for wheel install | `cp311-abi3-manylinux2014_aarch64` compatible with CPython 3.13 |
| `skyfield-data>=7,<8` | 7.0.0 | `orbits.py` accesses `files('skyfield_data')` and its version/packaged DE421 | pip data package; offline asset, no kernel download/system library | `py2.py3-none-any` |
| `numpy>=1.26,<3` | apt-managed on Linux ARM64; 2.5.3 snapshot elsewhere | `orbits.py` directly imports `numpy as np`; previously undeclared except transitively through Skyfield | **apt `python3-numpy` on Pi**, visible through system-site venv, linked distro numeric libraries; do not shadow the camera stack | Debian Trixie 2.2.4 is a target assumption, not a measured Pi version; PyPI also publishes CPython 3.13 ARM64 wheels |
| `tomlkit>=0.13,<1` | 0.15.1 | `config_edit.py` directly imports comment-preserving TOML parser/writer | pip, pure Python | `py3-none-any` |

The NumPy direct declaration was missing and is now fixed. There is no other
undeclared unconditional third-party runtime import. `jinja2`, `sgp4`, `tzdata`
and `skyfield-data` are consumed via adapters/loaders rather than simple top-level
import statements in every case; lack of a literal import is not lack of use.
Stdlib SQLite/SSL/threading/signal/path/tempfile/timezone APIs come with system
Python and its apt dependencies, not a separately installed Python SQLite package.

The active Linux core graph contained **27 distributions** including assumed
apt NumPy, with no conflicting version constraints or missing compatible wheels.
Its 16 transitive distributions (not separate application declarations) were:
`starlette==1.7.0`, `pydantic==2.13.5`, `typing-extensions==4.16.0`,
`typing-inspection==0.4.4`, `annotated-doc==0.0.5`,
`opentelemetry-api==1.45.1`, `click==8.5.0`, `h11==0.16.0`,
`MarkupSafe==3.0.4`, `anyio==4.15.1`, `certifi==2026.7.22`,
`httpcore==1.0.9`, `idna==3.20`, `jplephem==2.24`,
`annotated-types==0.8.0`, `pydantic-core==2.46.5`.
MarkupSafe and Pydantic Core publish `cp313` manylinux ARM64 wheels. Installing
these wheels requires no added Rust/C compiler. Source builds would require a
separate reviewed toolchain and are not part of this default procedure.

Primary metadata evidence includes
[SGP4 2.27](https://pypi.org/pypi/sgp4/2.27/json),
[Pydantic Core 2.46.5](https://pypi.org/pypi/pydantic-core/2.46.5/json),
[MarkupSafe 3.0.4](https://pypi.org/pypi/markupsafe/3.0.4/json) and
[Debian Trixie NumPy](https://packages.debian.org/trixie/python3-numpy).
The full credential-free metadata preflight JSON is ignored QA output at
`test-artifacts/pi-reaudit/dependency-metadata.json`. It explicitly says
`executed_on_pi=false` and records the assumed apt NumPy version.

Optional extras remain separate:

| Extra | Declarations | Status / role |
| --- | --- | --- |
| Gmail | `google-auth>=2.29,<3`, `google-auth-oauthlib>=1.2,<2` | Lazy OAuth/token/send dependencies; not installed by requirements.txt, no credential/mail test run |
| dev | `pytest>=8,<10` | Offline tests only |
| hrrr | `eccodes>=2.43,<3` | Optional sparse GRIB decoder; native ecCodes dependency, separate ARM64 decoder verification pending |
| qa | `playwright>=1.55,<2` | Optional browser automation only; no browser install required/performed for primary deployment |

## Apt packages and installation entry point

Complete **explicit package list in the documented bootstrap**:

```text
git ca-certificates tzdata curl avahi-daemon
python3 python3-venv python3-pip
rpicam-apps-lite python3-picamera2 python3-libcamera python3-numpy python3-pil
```

`git` is clone/update tooling; `curl` is verification tooling; `avahi-daemon`
supports the expected `.local` URL. Python/venv/pip are bootstrap tooling.
Picamera2/libcamera/NumPy/Pillow and rpicam are matched OS camera dependencies;
apt resolves their related native libraries/IPA/tuning/JPEG/PyAV dependencies
from the configured Raspberry Pi repositories. This list does not claim an
observed full apt transitive installation tree from a Pi. To record that tree
and actual versions there, run `apt-cache depends python3-picamera2
python3-libcamera rpicam-apps-lite` and `dpkg-query -W` during acceptance.

The camera package/utility choice follows
[Raspberry Pi's camera software documentation](https://www.raspberrypi.com/documentation/computers/camera_software.html)
and [Picamera2's apt recommendation](https://github.com/raspberrypi/picamera2#installation).
`python3 -m venv --system-site-packages .venv` exposes their system-Python modules
to the venv. Do not pip-install an alternate Picamera2/libcamera or use sudo pip.
Generic Debian alone is not the same as a configured Raspberry Pi OS image.

Keeping `requirements.txt` as `-c constraints.txt` plus `.` is technically correct:
the installer builds the local project and resolves its `pyproject.toml` runtime
declarations using the active constraints. The clean isolated Windows install
actually succeeded with that command, including packaging templates/static assets.
It remains unproved on the actual Pi until its native installer/import/startup
procedure succeeds. A long duplicate requirements file would not supply that proof.

The Python pin snapshot plus apt package inventory is reproducible in scope; this
is not a hash-locked complete OS image/build lock. Build tools are upgraded as
documented, and apt camera stack versions follow OS updates. Preserve the OS
NumPy rather than forcing the unrelated Windows snapshot into the camera venv.

## Platform findings and repairs

| Finding | Disposition |
| --- | --- |
| README short Pi intro followed immediately by PowerShell setup and many Windows operational commands | Rewritten with complete fresh-clone Pi bootstrap/config/manual/camera/systemd/LAN/testing/update/troubleshooting flow. Windows setup preserved in development-only guide; scientific/optional delivery details preserved in a Linux-command prediction guide. |
| Direct NumPy import absent from project declarations | Added `numpy>=1.26,<3`; retain existing apt-preserving Linux ARM64 constraint exception. |
| AGENTS/HANDOFF required only Windows pytest commands | Primary Linux command added; Windows development command retained, offline/privacy contracts unchanged. |
| Optional browser scripts defaulted to Edge everywhere | Edge remains Windows default; non-Windows uses Playwright Chromium or explicit channel. No browser dependency added to runtime. |
| A closed but never-started manager could subsequently open hardware | Start now rejects a manager whose stop event is set; offline test proves no hardware call. |
| Failed preview-mode restoration could leave manager reporting ready and retrying an uncertain device | Restoration now raises a camera fault, so the existing owner closes/releases and marks camera unavailable; prediction service remains independent. Offline test covers failure. |
| Timeout cleanup failure could hide uncertain device state | Mark backend failed before flushing; always classify timeout as a camera fault even if flushing fails. |
| Windows native-smoke child termination/temp cleanup raced the venv launcher | Test script uses a Windows process group and Ctrl+Break, with application shutdown log verification. POSIX mode sends SIGTERM; that branch remains unexecuted here. |
| No hardcoded drive paths, Windows subprocess/PowerShell calls or drive-letter concatenation in application source | Confirmed by source scan. Deliberate `os.name` branches implement `msvcrt` vs `fcntl` locking and POSIX token file mode; they preserve both platforms. |
| Runtime paths, temp files, SQLite, clocks and threading | Existing pathlib/config-relative paths, closed same-directory temporary replace, local WAL/busy timeout/per-operation connections, aware UTC and explicit serial workers retained. No multiprocessing or executable/shebang assumptions introduced. |
| LAN defaults are loopback | Safe default retained; documented validated `0.0.0.0`, LAN opt-in and exact hosts. Existing LAN route/trust tests pass. Actual allsky-pi4 routing remains pending. |
| Read-only UI/provider/scientific contracts | Retained; camera remains one owner with mockable lazy backend, bounded demand-only preview and separate page/schedule. No detector/profile/provider architectural changes or ZWO implementation. |

## Measured local validation

| Check | Evidence / limit |
| --- | --- |
| Clean non-editable runtime install | Succeeded in new Windows venv via `pip install -r requirements.txt`; existing .venv unchanged |
| README build-tool upgrade step | Succeeded in isolated venv: pip 26.2.1 / setuptools 84.0.0 / wheel 0.48.0 |
| Clean runtime `pip check` | No broken requirements |
| Installed module/import/assets | Installed path under isolated venv site-packages; runtime imports and private HTTP HTML/CSS/JS routes passed |
| Private config + SQLite + worker startup | `scripts/pi_native_smoke.py --require-installed` passed; all upstream providers and Gmail disabled; no live files touched |
| Local manual server shutdown | Windows Ctrl+Break and `Application shutdown complete` passed; POSIX SIGTERM explicitly skipped |
| Camera/settings targeted unit tests | 54 passed after camera guard/restoration repairs; mocked hardware, not a sensor test |
| Full offline regression suite | **263 passed**, 7 existing dependency deprecation warnings; final rerun 28.52 seconds |
| Compile/diff checks | `compileall` and `git diff --check` passed |
| Python 3.13/aarch64 dependency metadata | 27 core distributions with compatible wheel/marker/constraint metadata; apt NumPy version assumed, no native execution |
| Real Camera Module 3 / Picamera2 import in Pi venv | Pending; user's reported IMX708 detection is not a hardware test executed by this audit |
| systemd native parser/security/device validation | Template statically reviewed only; systemd-analyze/SIGTERM/device group acceptance pending |
| Existing fixture-browser QA | Passed at 360/390/768/1280 pixels, no page errors/overflow; preview/disconnect/focus/capture/download/timelapse/save/open-stream shutdown and owner release passed. No browser package was installed; fake hardware, not Pi acceptance. |

Windows sandbox temp/build restrictions required approved reruns for venv creation,
pip and private tests. These are environment restrictions, not genuine Linux
failures. The first smoke iteration had a Windows launcher/temp cleanup failure;
the corrected process-group shutdown passed. No tests were weakened.

Local commands used (Windows development syntax, not Pi installation instructions):

```powershell
.venv\Scripts\python.exe -m venv test-artifacts/pi-reaudit/install-venv
test-artifacts\pi-reaudit\install-venv\Scripts\python.exe -m pip --isolated install --keyring-provider disabled --index-url https://pypi.org/simple -r requirements.txt
test-artifacts\pi-reaudit\install-venv\Scripts\python.exe -m pip check
test-artifacts\pi-reaudit\install-venv\Scripts\python.exe scripts/pi_native_smoke.py --require-installed
.venv\Scripts\python.exe -m pytest tests/test_camera.py tests/test_lan_settings.py --tb=short
.venv\Scripts\python.exe -m pytest --tb=short
```

## systemd and remaining platform-specific material

The distributable unit was reviewed and retained: `User=ALL_SKY_USER`, configurable
substitution, project venv/working directory, networking order, `video`, failure
restart with ten-second delay/start limit, SIGTERM/300-second stop limit and journal
logs. No secrets, root execution, Windows syntax, PrivateDevices or blocked home
directory is embedded. On the actual Pi, verify the rendered paths and device-node
group ownership, run `systemd-analyze verify`, then manually install after hardware
acceptance. This audit does not enable/start systemd.

Remaining Windows material is intentionally confined to development-only instructions
and historical verification reports, plus this audit's actual Windows evidence.
AGENTS/HANDOFF list both interpreter paths. Optional browser scripts choose Edge
only on Windows. Runtime locking uses `msvcrt` only behind `os.name == 'nt'`, with
`fcntl.flock` on Linux; token permission code uses POSIX chmod conditionally.
Deprecated container smoke scripts retain container-only `os.getuid` / fixture
paths and are outside native acceptance. Workspace files are editor configuration,
not runtime launch logic. No primary Pi README command uses `python.exe` or a
Windows venv path.

## Remaining Pi acceptance

Do **not** declare migration complete until actual native execution succeeds:

1. Record Pi hostname/OS/aarch64/Python and inspect its existing venv safely.
2. Install documented apt packages and create the system-site venv with system
   Python; run the exact README build-tool/runtime install, `pip check` and imports.
3. Confirm apt NumPy/libcamera/Picamera2 origins and actual device/group permissions.
4. Run unit tests on the Pi; explicitly report optional ecCodes/browser skips.
5. Run `python scripts/pi_native_smoke.py --require-installed` for private offline
   installed-package HTTP/SQLite/worker/SIGTERM validation.
6. With other camera consumers stopped, run `rpicam-hello --list-cameras`, the
   diagnostic script, and `python scripts/pi_native_smoke.py --require-installed
   --camera` for preview/focus/still dimensions/three timed captures/stop/release.
   Inspect real image focus visually; metadata alone does not establish image quality.
7. Separately perform the README foreground run with operator-managed configuration
   and verify source health/local/LAN URL. Normal serve can fetch/send as configured;
   keep Gmail disabled during acceptance. This audit never opens production config.
8. Review/render/verify the unit; install/start it only as a separate manual action.

Expected deployment URL is **http://allsky-pi4.local:8008/**, not a measured Pi URL
from this session. CPU/CMA memory, storage throughput/retention, physical focus,
ARM native-library ABI, mDNS/client routing and actual systemd shutdown remain risks.
No auto image retention or camera reconnect exists. Future ZWO work must implement
an explicit backend with its own capabilities/SDK/ARM64 license validation and
device resource budget; it is not part of this audit.

Recommended commit message (no commit created):
`Make Pi setup self-contained and fix camera lifecycle faults`

## Files changed and final git status

The changes comprise deployment/development documentation, the direct NumPy
requirement, two camera lifecycle/error repairs and their offline tests, portable
optional browser defaults, and a private installed-package smoke script. The
actual systemd template, constraints, requirements entry point, example/live
config, provider/detector/profile/storage/network contracts were not altered.

Exact final `git status` (no staging or commit):

```text
On branch main
Your branch is up to date with 'origin/main'.

Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes in working directory)

        modified:   AGENTS.md
        modified:   HANDOFF.md
        modified:   README.md
        modified:   docs/phase2-verification.md
        modified:   docs/phase3-verification.md
        modified:   docs/phase4-operation.md
        modified:   docs/phase4-verification.md
        modified:   docs/pi-verification.md
        modified:   docs/raspberry-pi.md
        modified:   docs/verification.md
        modified:   pyproject.toml
        modified:   scripts/phase4_browser_smoke.py
        modified:   scripts/pi_camera_browser_smoke.py
        modified:   src/all_sky_alert/camera/backend.py
        modified:   src/all_sky_alert/camera/manager.py
        modified:   tests/test_camera.py

Untracked files:
  (use "git add <file>..." to include in what will be committed)

        docs/pi-reaudit.md
        docs/prediction-guide.md
        docs/windows-development.md
        scripts/pi_native_smoke.py

no changes added to commit (use "git add" and/or "git commit -a")
```
