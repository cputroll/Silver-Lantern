# Release gate

Before **any** release tag, build the exact zip using `release.ps1 -Tag v<version> -NoPublish`, run `qa/simulate-friend.ps1 -Zip <zip>` successfully, and obtain an independent review of the diff since the last tag from a different agent/session. Record reviewer sign-off in the PR, commit message, or `qa/reviews/`.

The GUI simulation sets `SDL_VIDEODRIVER=dummy` and `SDL_RENDER_DRIVER=software`, and requires the fixture install to reach the GUI's READY state. Safety checks sample descendants while each flow runs, but short-lived reparented processes can escape sampling; strengthen this process-tree audit before relying on it as exhaustive crash safety. Do not tag a release unless the entire gate passes.

For the prior-release check, pass `-OldZip` with a separately built test-enabled `0.0.1` zip: set `APP_VERSION`, the standalone launcher fallback version, and `sources.json` in an isolated copy, then build with `-Tag v0.0.1 -NoPublish`. The published v1.6.7 client predates the test seam.

`SILVER_LANTERN_GAME_MANIFEST`, `SILVER_LANTERN_TEST_GUI_INSTALL=1` (requires dummy SDL and a fixture manifest), `SILVER_LANTERN_TEST_ASSET_URL` (loopback only), and `SILVER_LANTERN_ALLOW_LOCAL_UPDATE=1` are **test-only** overrides. Never set them in production. Without the latter, unsigned local updates remain refused. Run against a locally built zip containing these seams. The published v1.6.8 zip predates them; validate published artifacts once a release containing them ships. The release script requires a tag matching `sources.json`, such as `-Tag v1.6.8`, not `-Tag qa`.
