# Release gate

Before **any** release tag, build the exact zip using `release.ps1 -Tag v<version> -NoPublish`, run `qa/simulate-friend.ps1 -Zip <zip>` successfully, and obtain an independent review of the diff since the last tag from a different agent/session. Record reviewer sign-off in the PR, commit message, or `qa/reviews/`.

**Gate incomplete: do not tag a release.** The runner fails closed for scenarios it cannot yet verify. It performs a real packaged CLI fixture install and stale-backup update; these do not substitute for the required end-to-end gate. Missing work:

- Zip-root GUI first run: a guarded dummy-driver automation path and isolated relocation check are present, but the relocated GUI has not yet completed its worker in verification.
- Interrupted download/resume: a throttled multi-bundle check is implemented but has not yet been verified end to end.
- Prior release update: published v1.6.7 predates and refuses the local-update test seam; a distinct older test-enabled binary must be built and verified before this flow can assert a version transition.
- Crash safety: process scanning currently checks executable paths within the artifact directory, not every descendant process, and scans profile logs rather than all application logs.

The real file-lock test currently fails: opening an existing locked destination for writing raises PermissionError before the replacement retry, and the packaged installer logs a traceback. The runner verifies uninstall against an isolated registry key and preserved save. Its safety scan runs after each check, but is not yet a complete process-tree audit.

`SILVER_LANTERN_GAME_MANIFEST`, `SILVER_LANTERN_TEST_GUI_INSTALL=1` (requires dummy SDL and a fixture manifest), `SILVER_LANTERN_TEST_ASSET_URL` (loopback only), and `SILVER_LANTERN_ALLOW_LOCAL_UPDATE=1` are **test-only** overrides. Never set them in production. Without the latter, unsigned local updates remain refused. Run against a locally built zip containing these seams. The published v1.6.8 zip predates them; validate published artifacts once a release containing them ships. The release script requires a tag matching `sources.json`, such as `-Tag v1.6.8`, not `-Tag qa`.
