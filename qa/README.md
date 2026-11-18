# Release gate

Before **any** release tag, build the exact zip using `release.ps1 -Tag v<version> -NoPublish`, run `qa/simulate-friend.ps1 -Zip <zip>` successfully, and obtain an independent review of the diff since the last tag from a different agent/session. Record reviewer sign-off in the PR, commit message, or `qa/reviews/`.

**Gate incomplete: do not tag a release.** The zip-root dummy-driver GUI relocates and logs `Test GUI: entering SDL initialization`, but does not reach `Test GUI: SDL renderer initialized` or the fixture worker before timeout (run 10). No real GUI screenshot was produced. The gate fails closed for that flow. Resume, file-lock, stale-backup, prior-version update and uninstall passed against a local package in run 9. Safety checks sample descendants while each flow runs, but short-lived reparented processes can escape sampling.

For the prior-release check, pass `-OldZip` with a separately built test-enabled `0.0.1` zip: set `APP_VERSION`, the standalone launcher fallback version, and `sources.json` in an isolated copy, then build with `-Tag v0.0.1 -NoPublish`. The published v1.6.7 client predates the test seam.

`SILVER_LANTERN_GAME_MANIFEST`, `SILVER_LANTERN_TEST_GUI_INSTALL=1` (requires dummy SDL and a fixture manifest), `SILVER_LANTERN_TEST_ASSET_URL` (loopback only), and `SILVER_LANTERN_ALLOW_LOCAL_UPDATE=1` are **test-only** overrides. Never set them in production. Without the latter, unsigned local updates remain refused. Run against a locally built zip containing these seams. The published v1.6.8 zip predates them; validate published artifacts once a release containing them ships. The release script requires a tag matching `sources.json`, such as `-Tag v1.6.8`, not `-Tag qa`.
