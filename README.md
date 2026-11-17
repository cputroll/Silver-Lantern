# Silver Lantern

Silver Lantern installs the game for you and keeps it up to date. The app installs itself into the game folder.

## What you need

- Windows 10 or 11
- About 40 GB of free disk space
- An internet connection

## Install

1. Download the latest release ZIP. No extraction needed - open `Run Silver Lantern.exe` directly from the ZIP; it installs itself into the game folder.
2. Alternatively, extract the ZIP anywhere you can write files and double-click `Run Silver Lantern.exe` in the extracted folder. Support files are in `support/`.
3. If Windows SmartScreen appears, select **More info -> Run anyway**. This warning can appear for a small app; only run a copy you trust.
4. Choose a folder and select **Download**. The first download is about 33 GB. Downloaded bundles are cleaned up automatically as installation proceeds.
5. When installation finishes, select **Play**.

**Advanced:** Run `SilverLantern.exe --builder-flow` to download the data package and open the Builder to compile locally, useful when prebuilt files are unavailable for your setup.

## Game settings (DLSS, FSR, frame generation, everything)

- **In game:** press **F1** or **Insert** to open the game's own settings menu (F11 toggles fullscreen, F10 the FPS counter). The game has no gamepad button for this menu; once it is open, the D-pad moves, A changes a value and B goes back.
- **To exit the game from that menu:** select **Quit**, the last row (just under **Restart**, on the same page as Text speed and Audio output), then press **A** or **Enter** twice. Esc or B cancels. Save first, because unsaved progress is lost.
- **In the launcher:** go to **Settings -> Graphics**. Every option the game reads is listed by group: Upscaling, Display, Performance, Interface, Gameplay, Controls, Motion and Advanced. Press A (or Enter, or click) to change a value, and left/right to step through values. Each change is saved to the game at once. **Recommended for this PC** applies the DLSS or FSR 3.1 preset that fits your graphics card, and **Reset to game defaults** restores the game's own values. Your controls and bindings are kept either way.
- The full option list is in [docs/graphics-options.md](docs/graphics-options.md).

## Keeping it up to date

The launcher checks for tool and game updates; use `SilverLantern.exe --self-update` to apply a tool update. The normal install does not rebuild anything on your PC.

## Uninstall

Uninstall from Windows Apps or run `SilverLantern.exe --uninstall`. Game files and launcher settings are removed, but the game folder and its `user` save directory remain in place. The confirmation shows the save path. To remove the saves and the entire game folder too, run `SilverLantern.exe --uninstall --purge-saves`. The older `--keep-saves` flag is still accepted and has no effect because keeping saves is now the default.

## All command-line options

- `--install-game [FOLDER]` installs or updates the prebuilt game files in the default folder or a folder you specify, without opening the game.
- `--dry-run` shows what the prebuilt install would download without installing the game; the tool may still update itself.
- `--prune` removes other files not in the game file list during a prebuilt install, except files in a `user` folder and certain manifest files.
- `--update-game [FOLDER]` runs the classic game updater for the default folder or a folder you specify; it may rebuild the game locally.
- `--update-on-launch` also runs a quiet check with the classic updater after the normal or Builder flow.
- `--builder-flow` downloads the data package and opens the Builder for a local build.
- `--graphics [FOLDER]` prints every game setting (current value, allowed values, default) as JSON. Add `--set KEY=VALUE` (repeatable) to change settings first, for example `--graphics --set frame_generation=Auto --set dlss=3`.

## Troubleshooting

- **Download interrupted?** Run the tool again; the download resumes.
- **Antivirus warning?** Security software can sometimes flag a small utility by mistake. Check that your copy came from a source you trust before allowing it.
- **Need the log?** Look for `HeyListen-Get.log` beside the EXE. If that folder cannot be written to, check `%LOCALAPPDATA%\SilverLantern\HeyListen-Get.log`. If neither file exists, capture the message shown in the window instead.
- **Want to check for updates now?** Run `SilverLantern.exe` again.
- **Moving the install?** The game folder defaults to `%USERPROFILE%\Games\HeyListen`. To install into another folder, run `SilverLantern.exe --install-game "D:\Games\HeyListen"` from a command window.
