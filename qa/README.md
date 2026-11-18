# Release gate

Before **any** release tag, build the exact zip using `release.ps1 -Tag v<version> -NoPublish`, run `qa/simulate-friend.ps1 -Zip <zip>` successfully, and obtain an independent review of the diff since the last tag from a different agent/session. Record reviewer sign-off in the PR, commit message, or `qa/reviews/`.

**Gate incomplete: do not tag a release.** The runner fails closed for scenarios it cannot yet verify. It performs a real packaged CLI fixture install and stale-backup update; these do not substitute for the required end-to-end gate. Missing work:

- Zip-root GUI first run: automate the GUI and supervise its relocated process in an isolated profile.
- Interrupted download/resume: throttle a multi-bundle fixture and prove previously completed parts are not fetched again.
- File lock: hold the destination executable exclusively from another process and prove the installer waits until release.
- Prior release update: obtain a distinct prior-version executable and prove the replacement reports the new version.
- Uninstall: create isolated registrations and automate confirmation, then verify saves and registration cleanup.
- Crash safety: enumerate Windows unhandled-exception dialogs and all descendant processes after every flow, and scan all logs for tracebacks.

`SILVER_LANTERN_GAME_MANIFEST`, `SILVER_LANTERN_TEST_ASSET_URL` (loopback only), and `SILVER_LANTERN_ALLOW_LOCAL_UPDATE=1` are **test-only** overrides. Never set them in production. Without the latter, unsigned local updates remain refused. Run against a locally built zip containing these seams. The published v1.6.8 zip predates them; validate published artifacts once a release containing them ships. The release script requires a tag matching `sources.json`, such as `-Tag v1.6.8`, not `-Tag qa`.
