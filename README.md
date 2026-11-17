# Silver Lantern

Silver Lantern installs the game for you and keeps it up to date.

## What you need

- Windows 10 or 11
- About 40 GB of free disk space
- An internet connection

## Install

1. Download the latest release ZIP and extract it anywhere you can write files.
2. Double-click `HeyListen-Get.exe` in the extracted folder.
3. If Windows SmartScreen appears, select **More info -> Run anyway**. This warning can appear for a small app; only run a copy you trust.
4. Wait for the progress bar while the game downloads. The first download is about 33 GB.
5. When installation finishes, the game opens automatically.

## Keeping it up to date

Every run checks for updates to both the tool and the game and downloads only what changed. Nothing is ever rebuilt on your PC.

## Troubleshooting

- **Download interrupted?** Run the tool again; the download resumes.
- **Antivirus warning?** Security software can sometimes flag a small utility by mistake. Check that your copy came from a source you trust before allowing it.
- **Need the log?** Look for `HeyListen-Get.log` beside the EXE. If that folder cannot be written to, check `%LOCALAPPDATA%\SilverLantern\HeyListen-Get.log`. If neither file exists, capture the message shown in the window instead.
- **Want to check for updates now?** Run `HeyListen-Get.exe` again.
- **Moving the install?** The game folder defaults to `%USERPROFILE%\Games\HeyListen`. To install into another folder, run `HeyListen-Get.exe --install-game "D:\Games\HeyListen"` from a command window.
