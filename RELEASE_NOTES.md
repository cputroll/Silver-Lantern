# Silver Lantern 1.3.0

## What's new

- **Smarter graphics preset.** The tool now looks at your graphics card. NVIDIA RTX cards that support DLSS get DLSS; every other card gets FSR 3.1 Performance.
- **Install game files directly.** `--install-game` downloads ready-made game files into `%USERPROFILE%\Games\HeyListen` (or a folder you choose). Each file is checked, and only missing or changed files are downloaded. `--dry-run` shows the plan without changing anything. `--prune` removes leftover files but never touches your saves.
- **Game updates.** `--update-game` updates an existing game folder to the latest version. `--update-dry-run` only checks, and `--force-update` rebuilds even when you are already up to date.
- **Much faster rebuilds.** Updates reuse the build cache, so they usually take minutes instead of about 35. Before building, the tool checks that you have enough free memory and disk space, and it backs up your saves first.
- **Automatic checks.** `--update-on-launch` quietly checks for a game update each time you run the tool. To check once a day, run `.\Watch-Game.ps1 -InstallTask` (remove it with `-UninstallTask`). You can also run `SilverLantern-AutoUpdate.cmd`.
- The download now includes `Update-Game.ps1`, `Watch-Game.ps1` and `SilverLantern-AutoUpdate.cmd`, which the update features need.

## Upgrading

If you have 1.2.0, it updates itself to 1.3.0 the next time you run it. You can also download `HeyListen-Get.zip` below and extract it over your old copy.
