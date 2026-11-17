# Silver Lantern: Controller-First Launcher Spec (`launcher.py`)

**Scope.** Written from a read-only pass over `Get-HeyListen.py` 1.3.0, `ui-spec.md`, `README.md`, `sources.json`,
`game-manifest.json`, `Update-Game.ps1`, `Watch-Game.ps1`, `AutoShip-Game.ps1`, `release.ps1`, `update-spec.md`.
Function names refer to `Get-HeyListen.py` 1.3.0; line numbers are omitted because the file keeps moving.

**Goal.** Open the app, use a controller (or keyboard/mouse) to press **DOWNLOAD**, then **PLAY**. The app checks itself
and the game for updates. Every screen is fully operable with a controller alone.

## First-run and installation requirements

Offer the local default `C:\Games\Legend of Zelda Ocarina of Time`, falling back to `%USERPROFILE%\Games\Legend of Zelda Ocarina of Time` if unwritable. Show approximately 40 GB required and verify writability. Offer adoption of the legacy `%USERPROFILE%\Games\HeyListen` in place; Settings can copy, verify and then delete the old folder. After installation create the per-user `Silver Lantern.lnk` using WScript.Shell COM, with no administrator privileges; target the game executable when present, otherwise the launcher, and let Settings remove or recreate it. The CLI uses explicit folder flags and only creates the shortcut with `--create-shortcut`. Quote all spaced paths at the PowerShell, subprocess, scheduled-task and shortcut boundaries listed in §6. Program artwork may use the official game logo subject to confirmed rights; release names and text remain neutral.

**Deliverables (implementing lane):** `launcher.py`, `test_launcher.py`, `assets/lantern.ico` (original art, §6.4).
`Get-HeyListen.py` is not owned by this lane (§4.3 says what it may do). §10 lists the cross-toolchain default-folder
change, which is a separate, coordinated change.

**Facts this spec relies on (verified):**
- `game-manifest.json` is v2: 186,615 files, `total_bytes` 35,483,046,179 (~33 GiB). It ships a **pre-built** game,
  including `HeyListen.exe` (root, 153 KB, the game's own launcher), `bin/HeyListen.exe` (the game), `bin/SDL2.dll`
  (300 KB) and `hookshot_version.txt`. It has **no `revision` field yet**; `AutoShip-Game.ps1` adds one on the next ship.
- So the default flow is `install_game()` (download pre-built files). `Update-Game.ps1` (local rebuild via the
  builder) is the optional **builder flow**.

---

## 1. Technology choice

| | **SDL2 via pysdl2 + pysdl2-dll** | PySide6 / Qt | pywebview (WebView2) | Dear PyGui |
|---|---|---|---|---|
| Controller support | Native `SDL_GameController` with a built-in mapping DB (Xbox, DualShock/DualSense, Switch Pro, 8BitDo, generic). Hot-plug events. | Qt 6 dropped QtGamepad; you still need SDL or XInput underneath. | Browser Gamepad API: polling only, the pad appears only after a button press, and input stops when the window loses focus. | ImGui has gamepad nav, but DPG barely exposes it: XInput only, no mapping DB. |
| D-pad, stick, A/B, analog repeat | Full raw events. We own the repeat and deadzone logic (§3), so it is testable. | n/a | Hand-written in JS | Fixed ImGui behaviour |
| PyInstaller onefile size (with curl_cffi ~10 MB) | **~16-20 MB** (SDL2 ~1.5 MB, SDL2_ttf ~1.5 MB) | 60-120 MB | ~25 MB, but WebView2 runtime required | ~25 MB |
| First run (no game folder yet) | Bundled SDL2.dll; nothing needed from the game | OK | Needs the WebView2 runtime (missing on some Win10 LTSC / N installs) | OK |
| Windows 10/11 | Win7+; D3D11/OpenGL/software renderer fallback | OK | Win10 1803+ with WebView2 | D3D11 required; no software fallback |
| Rendering effort | We draw rectangles and text ourselves (small, see §2.6) | Widgets free | HTML/CSS free | Widgets free |

**Recommendation: SDL2 via `pysdl2` + `pysdl2-dll` (with `sdl2.sdlttf`).** It is the only option where controllers are
first-class, it is small, it runs before any game files exist, and its software renderer works on GPU-less VMs.

- **Bundle our own `SDL2.dll` and `SDL2_ttf.dll`** (from `pysdl2-dll`, pinned version). **Never load the game's
  `bin\SDL2.dll`**: it does not exist on first run, it changes on game updates, and it is locked while the game runs.
  Set `os.environ["PYSDL2_DLL_PATH"]` to the bundle dir (`sys._MEIPASS` when frozen) **before** `import sdl2`.
- Font: bundle one OFL font (e.g. Inter or Noto Sans, Regular + Bold) under `assets/fonts/`, with its licence file.
- Renderer: `SDL_CreateRenderer(win, -1, SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC)`; on failure retry with
  `SDL_RENDERER_SOFTWARE`. Set hint `SDL_HINT_RENDER_SCALE_QUALITY=1`.
- Hints before `SDL_Init`: `SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS=0`, `SDL_HINT_GAMECONTROLLER_USE_BUTTON_LABELS=1`
  (Nintendo layout reports A on the right, as printed), `SDL_HINT_WINDOWS_DPI_AWARENESS=permonitorv2`.
- `SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMECONTROLLER | SDL_INIT_EVENTS)`. If GAMECONTROLLER init fails, continue
  without controllers (keyboard/mouse still work) and log it.

---

## 2. Screens and states

### 2.1 State machine (pure logic, `launcher_core.App`)

| State | Entered when | PLAY | DOWNLOAD | UPDATE | Notes |
|---|---|---|---|---|---|
| `STARTING` | process start | off | off | off | Resolve folder (§2.7), start background check (§5). Status `Checking for updates...` |
| `FIRST_RUN` | no `launcher.json` config (§2.8) | off | - | - | Folder screen (§2.7) before anything else |
| `NOT_INSTALLED` | folder has no `hookshot_version.txt` or no `bin\HeyListen.exe` | off | **on (focused)** | off | |
| `DOWNLOADING` | DOWNLOAD pressed | off | off | off | Progress screen |
| `READY` | install complete, or check finds nothing to do | **on (focused)** | off | off | Background check re-runs every 6 h while open |
| `UPDATE_AVAILABLE` | check found newer game manifest or tool | on | off | **on (focused)** | PLAY still allowed (plays current files) |
| `UPDATING` | UPDATE pressed (or auto-apply) | off | off | off | Progress screen |
| `PLAYING` | game process started | off | off | off | Status `Playing...`; window minimises; returns to `READY` on exit |
| `ERROR` | worker failed | - | - | - | Error screen; Retry returns to the state that failed |
| `RESTARTING` | tool self-update staged | off | off | off | `Updating Silver Lantern, restarting...`, then exit (§5) |

Transitions (the only legal ones; `App.dispatch(event)` raises `IllegalTransition` otherwise, which tests assert):

```
STARTING        -> FIRST_RUN | NOT_INSTALLED | READY
FIRST_RUN       -> NOT_INSTALLED | READY            (READY when an adopted folder is already complete)
NOT_INSTALLED   -> DOWNLOADING
DOWNLOADING     -> READY | ERROR | NOT_INSTALLED    (NOT_INSTALLED = cancelled)
READY           -> UPDATE_AVAILABLE | PLAYING | UPDATING(forced from Settings) | RESTARTING
UPDATE_AVAILABLE-> UPDATING | PLAYING | RESTARTING
UPDATING        -> READY | ERROR | UPDATE_AVAILABLE (cancelled; files stay valid, §4.4)
PLAYING         -> READY | UPDATE_AVAILABLE
ERROR           -> <state that failed>, or NOT_INSTALLED / READY after re-detection
```

PLAY is enabled only in `READY` and `UPDATE_AVAILABLE`. Disabled buttons are drawn dimmed and are **skipped by focus**.

### 2.2 Main screen

```
+--------------------------------------------------------------+
|  Silver Lantern                                   [pad icon] |
|                                                              |
|                 [   PLAY   ]                                 |
|                 [ DOWNLOAD ]                                 |
|                 [  UPDATE  ]                                 |
|                 [ SETTINGS ]                                 |
|                                                              |
|  Status: Ready to play.                                      |
|  Game data r12  |  Silver Lantern 1.4.0  |  C:\Games\...     |
|  (A) Select   (B) Back   (Menu) Settings                     |
+--------------------------------------------------------------+
```

- Buttons that do not apply to the state are **hidden**, not just dimmed, except PLAY, which is always shown (dimmed
  until `READY`). Order is always PLAY, DOWNLOAD, UPDATE, SETTINGS.
- Status line: one sentence, from §2.9 strings. Version line: `Game data r{revision or "1"}`, tool `APP_VERSION`, and
  the folder path middle-ellipsised to fit.
- Prompt bar (bottom): glyphs follow the active device (§3.5).

### 2.3 Progress screen

One bar, then `12.4 / 33.0 GB   41.7 MB/s   4:28 left`, then the current step (`Downloading game files`,
`Checking files (12,034 of 186,615)`, `Putting files in place`, `Applying graphics settings`), then **[ Cancel ]**
(focused; B also cancels after a confirm dialog "Stop the download? It picks up where it left off." [Stop] [Keep going]).
Speed: exponential smoothing alpha 0.2 over 1 s samples; ETA `--:--` for the first 3 s (same rules as ui-spec §2).
Window title mirrors progress: `Silver Lantern - 63%`.

### 2.4 Error screen

`Problem:` sentence, `What to do:` sentence (reuse the ui-spec §4 error table via `friendly(exc)` in `launcher_core`),
then **[ Retry ]** (focused), **[ View log ]** (opens the log in Notepad via `os.startfile`), **[ Back ]**. B = Back.

### 2.5 Update screen

Shown when UPDATE is pressed: "What's new" list (game: `r11 -> r12, 1.2 GB to download`; tool: `1.3.0 -> 1.4.0` plus
the first 8 lines of the GitHub release body, plain text, markdown stripped), then **[ Update now ]** (focused) and
**[ Later ]**. Tool update first if both are pending (it restarts the launcher, which then offers the game update).

### 2.6 Settings screen (vertical list; Left/Right change values, A toggles/activates)

| Row | Control | Effect |
|---|---|---|
| Game folder | value + `[Change...]` | Folder screen (§2.7) |
| Move game folder... | action | §2.8 move flow |
| Free space | read-only `212.4 GB free (about 40 GB needed)` | |
| Desktop shortcut | toggle On/Off | create / delete the .lnk (§6.5) |
| Update automatically | toggle (default Off) | auto-apply after the background check (§5) |
| Use builder flow | toggle (default Off) | `--builder-flow`: updates via `Update-Game.ps1` (local rebuild) instead of `install_game` |
| Check for updates now | action | runs §5 check immediately |
| View log | action | open the log |
| Back | action | B also goes back |

### 2.6a Drawing and layout

Logical canvas **1280x720**; `SDL_RenderSetLogicalSize(1280, 720)` scales to 1920x1080 and letterboxes other aspect
ratios. All coordinates in this spec are logical. Safe area: 64 px margin. Buttons 360x64, 16 px gap, font 28 px bold;
status 22 px; prompts 20 px. Focused button: 4 px accent outline + lighter fill + 1.04 scale; colors from a single
`THEME` dict. Background: flat dark or `assets/bg.png` (original art only, §6.4). Default window 1280x720, resizable,
`F11`/`Alt+Enter` toggles borderless fullscreen (desktop mode).

### 2.7 Install folder (first run and Settings)

**Folder name.** `GAME_DIR_NAME = "Legend of Zelda Ocarina of Time"`. This name is **local only**: it appears on the
user's disk and nowhere else. The repo, the EXE, the window title and the release text keep the public names
(Silver Lantern, "data package").

**Default resolution (`launcher_core.default_install_dir()`)**, pure, with filesystem calls injected for tests:

1. `PRIMARY = C:\Games\Legend of Zelda Ocarina of Time`. Try `mkdir(parents=True, exist_ok=True)`, then the
   writability probe (below).
2. If either step fails with `PermissionError`/`OSError` (ACL, no admin, C: policy), use
   `FALLBACK = %USERPROFILE%\Games\Legend of Zelda Ocarina of Time` and set the note
   `C:\Games isn't writable on this PC, so the game goes in your user folder instead.`
3. Do **not** create `PRIMARY` while only *showing* the default on screen. Create it only when the user confirms or
   when DOWNLOAD starts. If the probe fails, delete any empty folder it created.

**Writability probe (`probe_writable(path)`):** create `path`, write `.silverlantern-probe-<pid>` with 1 byte, `fsync`,
delete it. Pass only if all four succeed. Run it on the folder screen's Continue, and again just before DOWNLOAD.

**Free-space check:** `NEEDED_BYTES = manifest.total_bytes + 6 GiB headroom`, about 40 GB. Before the manifest is
fetched, use the constant `40 * 10**9`. Show it next to the path: `C:\Games\Legend of Zelda Ocarina of Time - 212.4 GB
free (about 40 GB needed)`, using `shutil.disk_usage` on the nearest existing ancestor. If space is short, the line
turns yellow, Continue stays enabled, and DOWNLOAD shows the ui-spec §2 disk-space message instead of starting.

**Folder screen** (FIRST_RUN, and `Change...` in Settings):

```
Where should the game go?
  > [ C:\Games\Legend of Zelda Ocarina of Time ]   212.4 GB free (about 40 GB needed)
    [ Use the existing folder: C:\Users\You\Games\HeyListen ]   (only when §2.8 detects it)
    [ Choose another folder... ]
    [ Continue ]
  (note line, if any)
```

- **Choose another folder...** opens the native picker (`IFileOpenDialog` with `FOS_PICKFOLDERS` via `ctypes`/`comtypes`,
  or `tkinter.filedialog.askdirectory` as a simpler fallback; tkinter adds ~3 MB). The picker is mouse/keyboard only;
  the screen's prompt bar says `Folder picker needs a mouse or keyboard`. Controller-only users can always take the
  default or adopt option. When the user picks a parent folder, the launcher appends `GAME_DIR_NAME` unless the picked
  folder is empty or already contains `hookshot_version.txt`.
- Reject paths on network drives (`GetDriveTypeW == DRIVE_REMOTE`), each with a friendly message.
- **Path-length rule (MAX_PATH):** reject a root when `len(str(root)) + 1 + MAX_REL + len(".heylisten-new") >= 260`,
  where `MAX_REL` is the longest `files[].path` in the manifest (`install_game` writes `<target>.heylisten-new` temp
  files). Before the manifest is fetched, use a cached value or a 200-character cap on the root. Skip the rule when
  `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled` = 1. The default root is 41 characters.
- The chosen folder is saved to the config (§2.8a) as `game_dir`.

### 2.8 Migration from the previous default (`%USERPROFILE%\Games\HeyListen`)

`LEGACY_DIR = %USERPROFILE%\Games\HeyListen` (the old `default_game_folder()`).

- **Detection** (first run only, no config yet): `LEGACY_DIR\hookshot_version.txt` exists, **or** `LEGACY_DIR\bin\HeyListen.exe`
  exists. A partial install also counts if `LEGACY_DIR\.heylisten-parts\` exists, because adopting it keeps the
  resumable downloads.
- **Adopt in place (default focus when detected):** the folder screen shows
  `[ Use the existing folder: ...\Games\HeyListen ]` as the **first, focused** option. Choosing it saves
  `game_dir = LEGACY_DIR`. Nothing is moved or renamed. The state is then decided by normal detection (READY when
  complete, otherwise NOT_INSTALLED, and DOWNLOAD resumes and fills gaps through `install_game`'s hash check).
- **Never forced.** The launcher never moves files on its own and never nags. Settings shows `Move game folder...`.
- **Move (Settings action, `launcher_core.move_game_dir(src, dst, progress, cancel)`)** runs in a worker:
  1. Refuse if the game or builder is running (process check as in `Update-Game.ps1` preflight), if `dst` is inside
     `src` (or the reverse), or if `dst` is not empty.
  2. Free space on `dst` must be at least the size of `src` plus 1 GiB, including on the same volume.
  3. On every volume, require enough free space for the full copy; never rename or delete the source first.
  4. Copy file by file into `dst` (`shutil.copy2`, preserving `user\` saves), with progress events.
     Then **verify**: for each file in the game manifest, compare size and SHA-256 (reuse `sha256()`); for files not in
     the manifest (`user\`, caches, `hookshot_options.ini`), compare size and SHA-256 against the source.
  5. Only after verification passes: save `game_dir = dst` in the config, recreate the shortcut (§6.5), then delete
     `src` with `shutil.rmtree`. If deletion fails part-way, log it and show `The old folder couldn't be fully removed:
     <path>. You can delete it yourself.` The move still counts as successful.
  6. Cancel or a verify failure: delete the partial `dst`, keep `src` and the config unchanged, and show the Error screen.

### 2.8a Launcher config

`%LOCALAPPDATA%\SilverLantern\launcher.json` (the same state dir `Update-Game.ps1` and `Watch-Game.ps1` use), written
atomically (temp file + `os.replace`):

```json
{"schema": 1, "game_dir": "C:\\Games\\Legend of Zelda Ocarina of Time", "auto_update": false,
 "builder_flow": false, "shortcut": true, "installed_revision": 12, "last_check": "2026-11-16T14:02:11Z"}
```

Missing or unreadable file means FIRST_RUN. Unknown keys are kept on rewrite.

### 2.9 On-screen strings (ASCII only, per ui-spec §6)

| Key | Text |
|---|---|
| `checking` | `Checking for updates...` |
| `not_installed` | `The game isn't installed yet. Press DOWNLOAD to get it (about 33 GB).` |
| `ready` | `Ready to play.` |
| `update_game` | `A game update is ready (r11 -> r12, 1.2 GB).` |
| `update_tool` | `A new version of Silver Lantern is ready (1.4.0).` |
| `offline` | `Couldn't check for updates (no connection). You can still play.` |
| `playing` | `Playing... Silver Lantern comes back when the game closes.` |
| `no_pad` | `No controller found. Plug one in, or use the keyboard and mouse.` |
| `pad_added` | `Controller connected: {name}` (3 s toast) |

---

## 3. Input model

### 3.1 Abstract actions

All devices map to one small enum, `Action = UP | DOWN | LEFT | RIGHT | ACCEPT | BACK | MENU | CLICK(x, y) | HOVER(x, y)`.
Screens consume only `Action`s, never SDL events. `launcher_input.InputMapper.feed(sdl_like_event, now) -> list[Action]`
and `InputMapper.tick(now) -> list[Action]` (for repeat) are pure functions of plain dicts, so tests inject events.

| Source | Mapping |
|---|---|
| Gamepad D-pad | `SDL_CONTROLLER_BUTTON_DPAD_*` -> UP/DOWN/LEFT/RIGHT |
| Left stick | axis LEFTY/LEFTX -> UP/DOWN/LEFT/RIGHT (deadzone §3.2) |
| Gamepad A (south) | ACCEPT. With `USE_BUTTON_LABELS=1`, Nintendo pads map the button labelled A |
| Gamepad B (east) | BACK |
| Start / Options / Menu | MENU (opens Settings from Main; acts as BACK inside Settings) |
| X (west) | CANCEL on the Progress screen: open the same safe cancel confirmation as B; ignored elsewhere |
| Back / View / Share | ignored |
| Keyboard | Arrows and WASD -> directions; Enter, Space, Keypad Enter -> ACCEPT; Esc, Backspace -> BACK; F1 or Tab -> MENU; Alt+Enter, F11 -> fullscreen toggle |
| Mouse | motion -> HOVER (moves focus to the hovered button); left button up on a button -> CLICK; wheel -> UP/DOWN |

### 3.2 Deadzone and repeat

- Stick: normalise to -1..1 (`value / 32767`). **Radial deadzone 0.35** to engage, **0.25** to release (hysteresis, so
  a resting stick doesn't flicker). Dominant axis wins; diagonals are ignored.
- Repeat (applies to D-pad, stick and arrow keys; the OS key-repeat is ignored via `event.repeat`): first Action on
  press, then repeat after **400 ms**, then every **120 ms**. Stick beyond 0.85 repeats every **70 ms**.
- ACCEPT and BACK fire on **release** for gamepad A/B (prevents a held A from the game carrying over into the launcher),
  on press for keyboard. ACCEPT never repeats.
- **Input guard:** ignore all input for 500 ms after the window regains focus and after returning from `PLAYING`.

### 3.3 Focus model

- Each screen has an ordered list of focusable widgets. UP/DOWN move to the previous/next **enabled** widget and stop at
  the ends (no wrap, so users can "slam" to the top). LEFT/RIGHT change the value of a Settings row, or do nothing.
- Entering a screen focuses its declared default (§2.1 table: DOWNLOAD in NOT_INSTALLED, PLAY in READY, UPDATE in
  UPDATE_AVAILABLE; Cancel on Progress; Retry on Error). When a state change disables the focused button, focus moves
  to the new default.
- **No focus traps:** every screen and dialog has a BACK target, except Progress, where BACK opens the cancel confirm.
  A test walks every screen and asserts BACK eventually reaches Main (§7).
- Modal confirm dialogs: two buttons side by side; LEFT/RIGHT move, default focus on the **safe** choice.

### 3.4 Controller lifecycle

- Open every `SDL_IsGameController` index on start and on `SDL_CONTROLLERDEVICEADDED`; close on `...REMOVED`. Any open
  controller drives input. Joysticks with no mapping are ignored (logged once with their GUID).
- No controller at startup: show `no_pad` in the prompt area for the session until one connects. Never block.
- Optional: `SDL_GameControllerAddMappingsFromFile(assets/gamecontrollerdb.txt)` if present (zlib licence, community DB).

### 3.5 Prompt glyphs

The prompt bar follows the last device used: Xbox-style `(A) Select (B) Back (Menu) Settings`; PlayStation
(`SDL_GameControllerGetType` = PS4/PS5) `(X) Select (O) Back (Options) Settings`; Nintendo `(A) Select (B) Back (+) Settings`;
keyboard `Enter Select  Esc Back  F1 Settings`; mouse hides the bar. Draw glyphs as text in a circle; no trademarked art.

---

## 4. Worker processes, progress and cancellation

The SDL event/render loop runs only on the main thread. Network, disk, hashing, process waits, shortcut COM, folder probing and update work run on worker threads. Workers never call SDL or mutate UI state: they put typed events into `queue.Queue`; the main loop drains it each frame. Give each operation a generation ID; ignore stale events after cancellation or a folder change. Allow only one install/update/move writer at a time.

Use a subprocess adapter rather than editing `Get-HeyListen.py`: execute the bundled CLI as an argument list with `shell=False`, redirect stdout and stderr, and read both concurrently without pipe deadlocks. Decode UTF-8 with replacement and split on carriage returns as well as newlines. Map `HLUPDATE key=value` using `parse_update_line`, and recognize friendly `Downloading: done / total`, SHA-256, plan, and phase lines; retain unrecognized lines in the log. Progress may be indeterminate: do not invent a percentage, speed or ETA. CLI `--update-game` currently consumes HLUPDATE internally, so either launch `Update-Game.ps1` directly for structured lines or parse the CLI's friendly output; its normalized exit code loses the distinction between 0 and 10. Both paths preserve user-facing errors in the log.

Queue events: `Step(id,text)`, `Progress(id,done,total,rate,eta)` (optional fields), `Log(id,line)`, `Completed(id,result)`, `Cancelled(id)`, `Failed(id,exception,log_path)`. Use monotonic time and coalesce progress to at most four updates per second; speed is a moving average of observed bytes and ETA is unknown until meaningful. Download command: `--cli --install-game <folder> --game-manifest <URL>`; builder update: `--cli --update-game <folder>`. No implicit prune. Bundle and dispatch `--cli` before SDL initialization; the adapter must start a child CLI process, not recursively enter GUI mode.

Cancel sets a cancellation event and terminates the owned CLI process tree using a Windows Job Object, waits in the worker, then emits Cancelled; never kill unrelated processes. Keep verified files and `.heylisten-parts` for resume. Cancellation during assembly or update can leave a mixed revision: remove no completed files, do not write the installed revision, and require a fresh verified sync before PLAY. Disable cancellation during the atomic self-update swap and explain why. CLI output capture alone cannot guarantee per-chunk cancellation inside current code; process-tree termination is the required boundary, tested on Windows.

## 5. Update policy

On launch start independent, quiet background checks: (1) query `latest_app_release(session, 'cputroll/Silver-Lantern')` and compare its stable tag to baked `APP_VERSION`; (2) fetch and validate the game manifest, compare its optional integer `revision` and a canonical SHA-256 of the validated manifest to the installed state; (3) run `Update-Game.ps1 -DryRun -GameFolder <folder>` for upstream HeyListen status, interpreting its raw exit 0/current, 10/available and 1/error plus `HLUPDATE` records. Skip upstream dry-run if not installed. This dry-run is not a published game-manifest update; display it separately and apply it only with builder flow enabled. A failed check is unknown, not current; retain offline play only for an already verified installation.

The current manifest has no revision: represent it as `unversioned` and compare canonical content hashes, never silently label it r1. On successful verified sync, atomically save `{revision: null|integer, manifest_hash, folder}` in launcher state; never advance it on cancellation, failure or dry-run. If a newly fetched manifest differs by hash, offer UPDATE even if revision is absent or unchanged. Save the actual manifest snapshot used by the install so concurrent publication cannot mark a different revision installed. A new or unrecognized adopted installation is `verification needed`, not proven current; run verified sync before marking READY.

Tool updates apply automatically when found: existing `check_self_update` is **check-and-apply**, not a quiet read-only check, so run its CLI `--self-update` path only after GUI coordinates shutdown and verifies the new release contains `SilverLantern.exe`. Existing code expects `HeyListen-Get.zip`, exact target basename in the archive, and an internal helper argument; the release pipeline must publish a compatible launcher-bearing archive and dispatch `--apply-update-internal` before SDL or this cannot work. Verify checksum, stage alongside target, wait for parent, replace transactionally, then relaunch GUI. Existing `--self-update` passes `--update-only-internal` and deliberately does not relaunch: the launcher needs a separate explicit relaunch helper protocol or coordinated backend change, not a claim that the existing flag restarts it. If unavailable, show an error and keep the old launcher; do not overwrite it with the CLI.

Game updates are applied only on UPDATE confirmation or when `auto_update` is enabled. If auto-update is enabled, start only after the check and while not PLAYING; defer while the game is active. A known pending game update leaves PLAY available as section 2 specifies, so this policy does not guarantee the newest version if the user defers or is offline. Show that clearly; never claim the game is current when check failed. Check again after returning from PLAYING and every six hours.

## 6. Packaging and launch

Build a Windows x64 PyInstaller `--onefile --windowed` executable named `SilverLantern.exe`, title `Silver Lantern`, with a version resource containing the baked application version and original icon `assets/silver-lantern.ico`. Bundle pinned SDL2/SDL_ttf DLLs and fonts, licence files, curl_cffi, CLI module, sources metadata and any needed scripts; SDL resolves the bundled DLL from the onefile extraction directory, not the nonexistent game directory. Verify loader architecture and Windows 10/11 startup from a clean user account. Include an independently executable console `HeyListen-Get.exe` if necessary to preserve existing CLI release/update compatibility.

`SilverLantern.exe --cli` dispatches to the existing console flow with remaining arguments, preserving exit codes. A windowed executable has no guaranteed standard handles; attach to the parent console or allocate one only when interactive, and preserve redirected handles for headless use. Folder selection in CLI is `--install-game "<folder>"` or the relevant game-dir flag. CLI does not create a shortcut unless `--create-shortcut` is explicitly passed. The GUI creates one after a successful install.

**Shortcut:** via `WScript.Shell` COM on a COM-initialized worker, resolve the per-user Desktop using Windows known-folder APIs. Create `Silver Lantern.lnk` with working directory set to the chosen game folder. Target `<folder>\bin\HeyListen.exe` when present, otherwise `SilverLantern.exe`; inherit the target icon. Settings toggle removes only this exact shortcut if it belongs to the launcher, or recreates it. Report failures without invalidating installation. Direct game targeting bypasses update checks: communicate that users should start Silver Lantern to check for updates; do not claim shortcut launches keep the game current. Optional, low priority: use an icon found in the user's local game folder for the local shortcut only, never upload it.

**Art policy:** the owner permits the official game logo in the program icon, shortcut and in-app art, subject to confirmed distribution rights; if rights are not confirmed, use an original lantern glyph. Public release names and text remain neutral.

**Paths checklist:** quote PowerShell `-File` and `-GameFolder` as separate subprocess-list elements; use lists and `shell=False` in every Python subprocess call; quote the complete executable path and arguments in `Watch-Game.ps1` task scheduler `/TR` command; use COM `TargetPath`, `WorkingDirectory` and properly quoted `Arguments` as separate properties. Test paths with spaces, Unicode and trailing separators. Update every default consistently in `Get-HeyListen.py` (`--game-dir`, `--install-game`, `--update-game`, `default_game_folder`), `Update-Game.ps1`, `Watch-Game.ps1`, `Apply-HeyListen-Preset.ps1`, and `AutoShip-Game.ps1`; coordinate with those files' owning agents. The local folder name does not change public branding.

## 7. Headless test plan

Keep state, input mapping, manifest comparison and event reduction in pure Python modules importable without SDL or an initialized GPU. Inject clock, filesystem, process factory, and network responses. Use `unittest` with fixtures: every state transition and illegal transition; disabled focus and back escape; d-pad, left-stick hysteresis/repeat, A/B/X/Start, keyboard and mouse; multiple pads and disconnect; ordered queue events, stale operation IDs, cancellation, failures and unknown progress; absent/equal/changed revisions and hashes, offline behavior, interrupted sync, successful revision commit and relaunch protocol.

Test folder fallback, write failures, disk shortfall, existing-folder adoption, copy/verify/delete failure safety, paths with spaces, COM shortcut target/toggle, and CLI flag routing with mocks. Test subprocess parsing with a fake child emitting `\r`, `\n`, stderr, HLUPDATE, exit 10, and no progress; verify process-tree cancellation and resumability on Windows. Headless CI uses fake SDL events and no renderer; Windows smoke tests separately exercise actual bundled DLL loading, software rendering, controller hotplug, per-user shortcut, GUI and CLI startup, self-update replacement and real game launch. Never download the game in CI.

## 8. Risks and unknowns

- Remote desktop or GPU-less machines: accelerated renderer may fail; try software, then show a native actionable error and allow CLI.
- HiDPI: set process awareness before window creation, use SDL logical coordinates, and test monitor changes, mixed DPI and 1280x720/1920x1080.
- SDL enumeration: unmapped, hot-unplugged, duplicate or reordered controllers and differing Nintendo button labels need hardware testing; keep keyboard/mouse available.
- Focus traps: all modal screens need an enabled Back/cancel path and deterministic focus restoration; test with controller only.
- Onefile DLL paths and Windows file locks need real packaged tests; bundle our own DLL, not a DLL only present after install.
- The upstream builder flow can launch non-controller GUI and is not equivalent to the published game manifest. Treat it as advanced; full controller-only flow requires the default prebuilt sync.
- Release asset replacement and manifest publication are not atomic; pinned hashes reject mismatched assets, but users may need to retry during publication. Never label a failed sync current.

