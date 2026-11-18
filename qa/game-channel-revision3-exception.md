# Game channel revision 3 — one-time owner-directed release

On 2026-11-18 the owner explicitly requested publication despite unverified publishing safeguards. This was a one-time exception, not approval to enable unattended AutoShip or a claim that crash recovery or cross-host serialization is fixed. The mutable legacy manifest was not changed.

The revision-3 manifest SHA-256 is `e4892110f22b0bbf1c7463e5c727f014fbe30a933130f295f68dec11c052b214`. All 22 staged asset lengths and SHA-256 hashes matched the manifest. A source-mode loopback install completed with exit 0 and an independent check of all 186,614 files found zero size/hash failures. The 22 GitHub release assets subsequently reported matching sizes and SHA-256 digests; a downloaded `game-manifest-3.json` matched the pointer digest.

The channel commit `efa75b3bbe6445ca9757aac630209301b4a84f87` has `1a5b5f41c87ee2d09212e763cef1ee3fcb2a1c13` as its parent and changes only `channel.json`. The operator used an exact-tip lease; the independent documentation reviewer verified the resulting parent and diff but could not independently reconstruct the historical lease from saved logs. GitHub API and raw channel responses both reported revision 3 and the expected manifest digest afterward.

Limitations: original staging provenance was not independently authenticated; reported GitHub asset hashes are not independent downloads of all 22 bundles; the production live-channel fresh install and gameplay are not yet verified. The automatic publisher and updater teardown safety holds remain in place.
