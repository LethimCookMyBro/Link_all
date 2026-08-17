# Runbook: Test isolation and dashboard lifecycle

- Symptom: The full suite emitted unawaited-coroutine and background-thread warnings; the dashboard test also failed intermittently with `NoMatches: #clients` during shutdown.
- Root cause: Two runtime dependencies were undeclared, two tests allowed mocked/real background work to outlive the test, and a queued dashboard refresh could run after its widgets were removed.
- Detection: Run `python -m pytest -q -W error::pytest.PytestUnraisableExceptionWarning -W error::pytest.PytestUnhandledThreadExceptionWarning`, then loop the dashboard headless test.
- Fix: Declare `PyNaCl` and `textual`, pass a plain sentinel to the mocked `asyncio.run`, mock both background module `start()` methods, and ignore refresh callbacks after the dashboard stops or loses its widgets.
- Regression tests: `CombinedLauncherUnitTests.test_combined_launcher_menu_options`, `PhantomLinkDeepCoverageComprehensiveTests.test_main_startup_sequence`, and `TestDashboardApp.test_late_refresh_after_shutdown_is_ignored`.
- Monitoring: Waived; this is a local test/lifecycle defect with no deployed metrics endpoint.
