# recovery.4 RC2 — Packaging fix evidence

Windows smoke testing of the previous RELEASE CANDIDATE exposed a fresh-install defect:

1. PyInstaller built successfully, but `engine_updates` was not included in the dist.
2. Even if the bundle were present, the managed installer allowed only current Engine `0.3.12`; a clean registry starts at `lion-rebar-bootstrap v0.0.0`, so Engine `0.3.13` could not activate.
3. The installer instructed the user to run `START_LION_REBAR.bat`, but that file was absent.

RC2 fixes all three without changing recognition behavior.

Expected clean Windows smoke after build:
- application starts;
- bottom status shows `lion-rebar-master v0.3.13` and production state;
- MASTER Studio lists/activates Engine 0.3.13 rather than bootstrap as current;
- no manual MASTER import is required.

Regression evidence in this environment:
- fresh bootstrap -> trusted bundled 0.3.13 test: PASS;
- recognition pair + F006 production split + AUTO lifecycle combined: 25 PASS.

Final Windows dist smoke remains required before calling the package FINAL RELEASE.
