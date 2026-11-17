# Silver Lantern

Silver Lantern installs the game for you and keeps it up to date.

## What you need

- Windows 10 or 11
- About 40 GB of free disk space
- An internet connection

## Install

1. Download the latest release ZIP and extract it anywhere you can write files.
2. Double-click `SilverLantern.exe` in the extracted folder.
3. If Windows SmartScreen appears, select **More info -> Run anyway**. This warning can appear for a small app; only run a copy you trust.
4. Choose a folder and select **Download**. The first download is about 33 GB. Downloaded bundles are cleaned up automatically as installation proceeds.
5. When installation finishes, select **Play**.

**Advanced:** Run `SilverLantern.exe --builder-flow` to download the data package and open the Builder to compile locally, useful when prebuilt files are unavailable for your setup.

## Keeping it up to date

The launcher checks for tool and game updates; use `SilverLantern.exe --self-update` to apply a tool update. The normal install does not rebuild anything on your PC.

## All command-line options

- `--install-game [FOLDER]` installs or updates the prebuilt game files in the default folder or a folder you specify, without opening the game.
- `--dry-run` shows what the prebuilt install would download without installing the game; the tool may still update itself.
- `--prune` removes other files not in the game file list during a prebuilt install, except files in a `user` folder and certain manifest files.
- `--update-game [FOLDER]` runs the classic game updater for the default folder or a folder you specify; it may rebuild the game locally.
- `--update-on-launch` also runs a quiet check with the classic updater after the normal or Builder flow.
- `--builder-flow` downloads the data package and opens the Builder for a local build.

## Troubleshooting

- **Download interrupted?** Run the tool again; the download resumes.
- **Antivirus warning?** Security software can sometimes flag a small utility by mistake. Check that your copy came from a source you trust before allowing it.
- **Need the log?** Look for `HeyListen-Get.log` beside the EXE. If that folder cannot be written to, check `%LOCALAPPDATA%\SilverLantern\HeyListen-Get.log`. If neither file exists, capture the message shown in the window instead.
- **Want to check for updates now?** Run `SilverLantern.exe` again.
- **Moving the install?** The game folder defaults to `%USERPROFILE%\Games\HeyListen`. To install into another folder, run `SilverLantern.exe --install-game "D:\Games\HeyListen"` from a command window.
