# Release gate

Before **any** release tag, build both artifacts from the same revision using `release.ps1 -Tag v<version> -NoPublish` and `release.ps1 -Tag v<version> -NoPublish -Qa`. The production artifact is `dist-release/HeyListen-Get.zip`; the QA artifact is `dist-release/HeyListen-Get-qa.zip` and must never be published.

Run `qa/simulate-friend.ps1 -Zip dist-release/HeyListen-Get-qa.zip` for fixture flows and `python qa/check_production_trust.py --zip dist-release/HeyListen-Get.zip` to assert the actual release executable ignores all three trust-sensitive seam variables. Both checks must pass. Obtain an independent review of the diff since the last tag from a different agent/session and record sign-off in the PR, commit message, or `qa/reviews/`. Do not tag until the entire gate passes.

The GUI simulation sets `SDL_VIDEODRIVER=dummy` and `SDL_RENDER_DRIVER=software`, and requires the fixture install to reach the GUI's READY state. Safety checks sample descendants while each flow runs, but short-lived reparented processes can escape sampling; strengthen this process-tree audit before relying on it as exhaustive crash safety.

For the prior-release check, pass `-OldZip` with a separately built QA-enabled `0.0.1` zip: set `APP_VERSION`, the standalone launcher fallback version, and `sources.json` in an isolated copy, then build with `-Tag v0.0.1 -NoPublish -Qa`.

`SILVER_LANTERN_GAME_MANIFEST`, `SILVER_LANTERN_TEST_ASSET_URL` and `SILVER_LANTERN_ALLOW_LOCAL_UPDATE` work only in QA builds or source runs. `SILVER_LANTERN_TEST_GUI_INSTALL=1` also requires dummy SDL and a fixture manifest. Production builds bake `QA_SEAMS=False`; inherited environment variables cannot enable these trust overrides. Source runs default to QA seams when no generated flag module is present.
