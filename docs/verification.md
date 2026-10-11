# Verification of the initial slice

Windows Python 3.12; dependency versions recorded in constraints.txt.

- 47 offline tests pass. Network transports are blocked in normal tests; Gmail
  tests use fake credentials and HTTP transports and send no actual mail.
- compileall and pip check pass.
- Live Open-Meteo evaluation for the explicitly documented demonstration observer
  produced 34 upcoming planetary/sunset predictions with fresh weather.
- Running localhost service returned HTTP 200 for homepage, JSON prediction list,
  and event detail. Automated tests cover HTML escaping, unsafe/malformed source
  links, unknown events, explicit host allowlist, historical revision reads and
  the exact two-hour recent-history query boundaries.
- Linux x86_64 Docker image built successfully. Offline execution as UID 10001
  produced 14 predictions with weather disabled. A second container reused the
  mounted SQLite directory and verified packaged homepage, event detail and CSS.
- No browser surface was available to the UI automation tool, so visual screenshot
  verification could not be performed. Browser HTML functionality was verified
  through HTTP and ASGI tests. Synology hardware and ARM images remain untested.
- One upstream Starlette test-client deprecation warning remains for HTTPX 0.28;
  the pinned combination passes. Review its test client migration when updating
  dependencies rather than suppressing the warning.
- No OAuth consent was initiated, real email sent, secrets inspected, deployment
  published or git commit created. Reference-project code was not copied.

See README.md for repeatable launch/test commands and docs/extension-review.md for
the architecture's future-detector review and deliberately deferred features.
