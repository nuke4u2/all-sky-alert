# Bounded developer handoff

Primary deployment is now native Raspberry Pi OS Lite 64-bit/Trixie with a system
Python venv and systemd; Synology/Docker is deprecated and unmaintained. Read
[Pi operation](docs/raspberry-pi.md), [camera design](docs/camera-architecture.md)
and [verification](docs/pi-verification.md) for camera/deployment handoffs.
Camera Module 3 is **General Pi Camera / Timelapse**, not the deferred ZWO All-Sky
Camera. Camera tasks may touch explicitly allowed `camera/` modules and UI/tests,
but must keep one hardware owner, bounded previews/commands, offline fake-backend
tests and prediction/provider isolation. Saved TOML requires restart; explicit
camera session controls do not reload config. Never give a local handoff actual
config, camera captures, secrets, mail or permission to install/enable systemd.

Copy this template into a task document (e.g. docs/handoffs/task-name.md). Supply
explicit paths and acceptance criteria. Completion returns a diff and report for
Codex/Astra review; no commits, pushes or merges by default. This is a development
convention, not an application runtime feature.

For source/detector work, include the relevant rows of
[source-capabilities.md](docs/source-capabilities.md) and
[Phase 2 contracts](docs/phase2-architecture.md).
[Phase 3 contracts](docs/phase3-architecture.md) apply to orbital/aircraft work.
Keep normal tests offline, source HTTP stop/epoch/position gates intact, and
user-licensed enrichment optional. Numeric transit tolerance is not a physical
uncertainty guarantee. Do not expand into generic launch/reentry visibility.
Identify its worker lane,
source timestamps/expiry, complete reconciliation scope and stable episode identity.
Provider handoffs must specify response-size limits, cache keys, data validation,
backoff, atomic snapshot publication and offline fixtures. Do not interpret an
HTTP 200 as fresh observations, a native category as physical seeing, or a catalog
prediction as a confirmed sighting/photometric measurement. A bounded handoff
does not authorize live smoke tests, secrets, paid services or architecture changes.

```markdown
# Task: <bounded objective>

## Outcome
<observable behavior and specific acceptance criteria>

## Architecture context
<where the change sits; link docs/architecture.md and relevant contracts>

## Allowed files
- <explicit modules/tests/docs that may be changed>

## Protected files and access
- <explicit files/modules that must not be changed>
- No secrets/, data/, OAuth client/token files, Gmail/GitHub credentials,
  unrelated directories, pushes, merges, commits or history rewriting.

## Invariants
- <stable identity, UTC/time ordering, unknown handling, score separation, etc.>
- No changes to core contracts/architecture unless specifically authorized here.
- No runtime LLM dependencies or orchestration infrastructure.

## Required verification
- <targeted offline tests and meaningful failure cases>
- Linux: .venv/bin/python -m pytest
- Linux: .venv/bin/python -m compileall -q src tests
- Windows development: .venv\Scripts\python.exe -m pytest
- Windows development: .venv\Scripts\python.exe -m compileall -q src tests

## Sources and context
- <specific docs, source URLs, fixture definitions, prior decisions>
- Credential-free/read-only web research is allowed; no live API/mail tests.

## Deliverable
<implemented diff, test results, limitations, concise notes for architectural review>
```

Example bounded task: add independent tests for the sunset profile's missing
cloud-layer behavior and 100% low-cloud obstruction. Allowed: tests/test_domain_assessment.py
only. Protected: all src/, configuration, deployment and core docs. Invariants:
no live network, intrinsic interest untouched, unknown metrics never treated as
zero cloud. Deliver a passing test diff and report any discovered source-code bug
to Codex/Astra rather than expanding the scope without authorization.

Phase 4 context: include `docs/phase4-architecture.md` and
`docs/phase4-operation.md` for map/health/settings/optics tasks. Actual config is
protected unless explicitly allowed. Use private TOML/SQLite fixtures for settings
work. Browser QA is optional `.[qa]` with `scripts/phase4_browser_smoke.py`;
external tiles are disabled. Live HRRR probes require explicit task scope and must
honor byte/run limits. Never supply production OAuth or database access to a local
agent. A bounded task cannot change LAN/proxy trust, notification identity or
scientific freshness contracts without Codex/Astra review.
