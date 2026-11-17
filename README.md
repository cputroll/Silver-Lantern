# Silver Lantern

Silver Lantern downloads a data package and prepares the Builder for you on Windows.

## Getting started

1. Extract the downloaded ZIP into a folder you can write to, then open **HeyListen-Get.exe**.
2. If Windows SmartScreen warns you, select **More info**, then **Run anyway**. This warning can appear for a small, unsigned app; only run a copy you trust.
3. Wait for the download to finish. The data package and Builder are saved in your **Downloads** folder. Allow about **26 GB** of free space for the download and about **60 GB** while building.
4. In the Builder window:
   1. Pick the downloaded data package file.
   2. Tick **NVIDIA DLSS** if your graphics card is an NVIDIA RTX.
   3. Press **Build**.

The tool picks a graphics preset for you: NVIDIA DLSS on RTX cards that support it, otherwise FSR 3.1 Performance.

## Installing game files

To download ready-made game files straight into a folder, run:

    HeyListen-Get.exe --install-game

- The default folder is `%USERPROFILE%\Games\HeyListen`. To use another folder, add it after the flag: `--install-game "D:\Games\HeyListen"`.
- Every file is checked. Only missing or changed files are downloaded, so running it again is quick and an interrupted download picks up where it stopped.
- Add `--dry-run` to see what would be downloaded without changing anything.
- Add `--prune` to also remove files that are no longer part of the game. Your saves in the `user` folder are never removed.

## Keeping it current

- **Update the game now:** `HeyListen-Get.exe --update-game` updates the game in the default folder. You can also give it a folder: `--update-game "D:\Games\HeyListen"`.
- **Just check:** `HeyListen-Get.exe --update-game --update-dry-run` tells you if an update is available and changes nothing.
- **Check every time you run the tool:** add `--update-on-launch`. After its normal work, the tool quietly checks whether a game update is available.
- **Daily check:** in PowerShell, from the tool's folder, run `.\Watch-Game.ps1 -InstallTask`. Windows then checks once a day. Remove it with `.\Watch-Game.ps1 -UninstallTask`.
- Rebuilds reuse the build cache, so an update usually takes minutes instead of about 35.
- The tool also updates itself when a new version is released.

## Troubleshooting

- **Download interrupted?** Run the tool again; it resumes the download where possible.
- **Antivirus warning?** Security software can sometimes flag small, unsigned utilities by mistake. Check that your copy came from a source you trust before allowing it.
- **Need the log?** Look for `HeyListen-Get.log` beside the tool. If that folder cannot be written to, check `%LOCALAPPDATA%\SilverLantern\HeyListen-Get.log`. If neither file exists, capture the message shown in the window instead.
