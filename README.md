# Silver Lantern

Windows utility for setting up HeyListen from a compatible game data file.

## Use

Extract the release archive into a writable folder and run **HeyListen-Get.exe**. On startup it checks this repository's latest release and installs a newer executable when available. If the check fails, the current version remains available.

The bundled `sources.json` configures downloads; its remote manifest URL allows source changes without an executable release. Checksums protect against accidental corruption, but are not a substitute for signed releases.

For a new release, update the embedded application version and manifest version together, then push the matching version tag. The release workflow builds the Windows executable, packages it and publishes checksums.
