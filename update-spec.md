# HeyListen: updating a built game folder without a full rebuild

Status: research spec, 2026-11-16. Built from the machine's files (game folder, builder install, `make_game.py` v0.4.0)
and upstream `Sacrappy/HeyListen` (docs/USER_GUIDE.md, engine/readme.md, the GitHub releases API).

Current state on this machine: game folder `C:\Users\ghostdma\Games\HeyListen` was built with **v0.4.0**, and upstream
`releases/latest` is **v0.4.0** (published 2026-11-16T06:14Z). **No update is due today.** With the contract below, the
script would exit 0.

---

## 1. `hookshot_version.txt` and how to tell "installed X, latest Y"

### What the file holds

`make_game.py` writes it at the end of every successful build or update (lines 1671-1687). It is UTF-8 with LF line
endings and `key=value` lines:

```
version=v0.4.0
builder=C:\Users\ghostdma\AppData\Local\HeyListenBuilder\versions\v0.4.0\HookshotBuilder\HeyListenBuilder.exe
```

- `version=`: the first line of `<builder>\HookshotBuilder\tools\version.txt` (on this machine `v0.4.0`), so it is the
  **builder release tag** that built the folder, not a game version.
- `builder=`: `HOOKSHOT_BUILDER_EXE` at build time, which is the builder that made the folder. The in-game update offer
  (`host/hos_update.c`) reads this line.
- `builder_args=`: an optional line, written only when `HOOKSHOT_BUILDER_ARGS` was set (the Python builder).
- The file is written only when a tag was found. A folder without it is either unbuilt or was built from a dev checkout.

### Other markers in the game folder (what they mean)

| File / dir | Meaning | Update uses it? |
|---|---|---|
| `.hookshot_exefs\` (main, sdk, subsdk0, rtld, main.npdm; ~89 MB) | The game's code as read from the .nup. `--update` copies it into the work folder and builds from it | **Required** |
| `romfs\` | The game's data (hard-linked where possible). | **Required** |
| `.hookshot_romfs` | Stamp `unpacked by this builder: <nup name> <nup size>`. Lets a `--nup` rebuild skip unpacking again | No (`--update` path only) |
| `.hookshot_romfs_checked` | The same stamp, written after the romfs check passed | Read |
| `.hookshot_code_mods` / `.hookshot_fsr` | Remembered code-mods and FSR options. **Neither exists here** (no code mods, no FSR) | Read |
| `bin\`, `image.bin`, `HeyListen.exe` (launcher), `ngx\` | The build outputs | Replaced |
| `language.txt` | The game language (`en-US`). `--update` reads it (`folder_language`) and writes it again | Read, then rewritten |
| `hookshot_options.ini` | The in-game menu's choices | Kept |
| `hookshot_compiled.bin`, `hookshot_programs.bin/.idx` (6.9 GB), `hookshot_pipelines.bin`, `mkw_vk_pipelines.bin`, `hookshot_pipeline_states*.bin`, `hookshot_shaders.bin`, `hookshot_prepared.bin` | Shader and pipeline caches built by the shader preparation and at runtime. Per the user guide, a cache that no longer matches is prepared again at the next start | Do not delete; the game and the build manage them |
| `user\` (saves) | Does not exist yet: the game has not been started | **Never touched** |

### A reliable way to detect an update

1. **Installed version:** read `<GameFolder>\hookshot_version.txt` and take the `version=` line. If the file or the
   line is missing, report `installed=unknown` and exit with error code 1. Do not guess.
2. **Latest version:** `GET https://api.github.com/repos/Sacrappy/HeyListen/releases/latest`, sent with
   `Accept: application/vnd.github+json` and a `User-Agent`, and take `tag_name`. This is the URL that both
   `HeyListenBuilder.dll` and the launcher `HeyListen.exe` call. **Do not take the first item of `/releases`**: the
   repository also has prerelease tags `linux-parts` and `builder-parts`, which hold parts, not versions.
   `/releases/latest` already skips prereleases.
3. Compare the two as semver (`vMAJOR.MINOR.PATCH`, with the `v` removed). An update is due only when latest > installed.
   If latest < installed, report it and do nothing.
4. Optional check that the builder is present: `%LOCALAPPDATA%\HeyListenBuilder\versions\current.txt` names the
   installed builder (`v0.4.0`), and `versions\<tag>\installed.txt` holds the tag once that version is fully unpacked.
   The builder can be newer than the game folder, for example when the builder was updated but the rebuild failed. In
   that case skip the download and only rebuild.

---

## 2. How to update with the least work

### Where the reusable state lives

Builder root: `%LOCALAPPDATA%\HeyListenBuilder`. This is the `--work` folder that the Windows builder passes to `make_game.py`.

| Path | Size | What it is |
|---|---|---|
| `versions\current.txt` | `v0.4.0` | The active builder version |
| `versions\v0.4.0\installed.txt` | `v0.4.0` | Written once the version is fully unpacked |
| `versions\v0.4.0\parts.txt` | 5 SHA-256 values | The hashes of the large tool parts (python, emsdk, cpm, wgpu-native, llvm-mingw), probably used to reuse unchanged parts in the next version |
| `versions\v0.4.0\HookshotBuilder\` | ~1.56 GB, 12,967 files | The .NET GUI (`HeyListenBuilder.exe` apphost plus `.dll`) and `tools\` (python, llvm-mingw, emsdk, cpm, wgpu-native, third_party, nx2, host, gpu, native, scripts, work, `version.txt`) |
| **`objcache\`** | **670 MB, 695 files** | **Compiled objects from the last build. This is what makes recompiles incremental.** After a build, `prune_object_cache` keeps only the objects that build used |
| `system_headers.txt` | 42 B | A hash of the compiler's system headers. **If it changes, `objcache` is deleted** (`make_game.py` lines 750-765) |
| `ngx-sdk\` | 63 MB | NVIDIA's DLSS SDK, already fetched (`builder-settings.json`: `"Dlss":true`) |
| `hookshot.res`, `hookshot_pieces.prof`, `hot_funcs.txt`, `hot_order.txt`, `image\` | ~15 MB | Profile and link-order inputs |
| `build_times.json` | | The record of build times. Last build: `kind=build`, total 1720.6 s, compile 1338.7 s |
| `builder-settings.json` | | The GUI's choices: Out, Nup, Dlss=true, CodeMods=false, Fsr=false, PrepareLater=false |

`sa\` (the translated C, ~1.5 GB) and `probe\` were **deleted** after the build because `HOOKSHOT_KEEP_BUILD_FILES` was
not set. As a result, **an update always translates (lifts) again**, which takes minutes, and then **compiles only the
objects whose key changed**.

Note: the upstream readme's "Updating" section says an update "takes about as long as the first build". That text is
older than issue 1287. The v0.4.0 `make_game.py` keeps `objcache` on purpose so that updates compile only what changed
(see the comment at lines 1688-1697, and the GUI string "about 1 GB stays (the compiled code) so that updates compile
only what changed").

### What the Windows builder accepts on the command line

The UTF-16 strings in `HeyListenBuilder.dll` include `--fsr --language --launch --no-prepare-shaders --out --romfs` and
`GetCommandLineArgs`. **They do not include `--update`, `--cli` or `--jobs`**, so the Windows GUI has no headless
update. (`--cli`, `--update <folder>` and `--play` belong to the **Linux AppImage** only.) The game launcher
`HeyListen.exe` contains `--launch "%ls"`, `--repair` and `--tag`, together with the GitHub `releases/latest` and
`heylisten-setup.txt` URLs. It is the program that downloads a release and starts the builder, but the meaning of its
flags is not documented, so a script should not rely on it.

The supported headless path is therefore **the same script the GUI runs**: `tools\nx2\make_game.py --update`.

### Phase A: install the new builder version (skip it if `versions\<latest>\installed.txt` already exists)

The release asset `heylisten-setup.txt` (at `https://github.com/Sacrappy/HeyListen/releases/download/<tag>/heylisten-setup.txt`)
is the parts manifest:

```
tag v0.4.0
part <sha256> <size> <dest> <strip|-> <rename|-> <url>     (8 parts; for example llvm-mingw goes to HookshotBuilder/tools as llvm-mingw)
run HookshotBuilder/HeyListenBuilder.exe
```

There are two options:

- **A1 (recommended): use the official bootstrapper.** Download the release asset `HeyListenBuilder.exe` (the
  bootstrapper; check it against the release's `SHA256SUMS.txt`) into a new empty temporary folder and run it. It
  downloads only the missing parts, checks each one by SHA-256, unpacks them into `versions\<tag>\`, writes
  `installed.txt` and `parts.txt` and updates `current.txt`. Then it opens the GUI.
  - Automation: poll until `versions\<tag>\installed.txt` contains `<tag>` (allow up to 30 minutes for about 470 MB in
    the worst case). Then close the GUI window that the `run` line started (the `HeyListenBuilder` process whose path is
    under `versions\<tag>`) with `CloseMainWindow()`, and kill it only after a timeout.
  - Alternatively, leave the GUI open: its start page shows **Update** for a folder it knows. Pressing it is the GUI
    route (see the fallback below).
- **A2: install the parts yourself** (no GUI at all). For each `part` line: download into an empty temporary folder,
  check the size and SHA-256, unzip into `versions\<tag>\<dest>` (applying the `strip` and `rename` fields), then write
  `parts.txt`, then `installed.txt`, and only then `current.txt`. A part whose hash is listed in an older
  `versions\*\parts.txt` can be copied from that version instead of downloaded. **The column meanings are inferred, not
  documented** (see Risks), so A2 is a fallback only.

### Phase B: rebuild the folder incrementally

Run from `T = %LOCALAPPDATA%\HeyListenBuilder\versions\<latest>\HookshotBuilder\tools`. **The working directory must be
`T`**, because `make_game.py` runs `nx2/build.py` and `nx2/nup.py` by relative path.

```powershell
$B   = "$env:LOCALAPPDATA\HeyListenBuilder"
$tag = (Get-Content "$B\versions\current.txt").Trim()
$T   = "$B\versions\$tag\HookshotBuilder\tools"
$env:HOOKSHOT_BUILDER_EXE = "$B\versions\$tag\HookshotBuilder\HeyListenBuilder.exe"  # keeps the builder= line correct
$env:HOOKSHOT_NGX_SDK     = "$B\ngx-sdk"            # REQUIRED here: the folder was built with DLSS; without it the renderer is built without DLSS
$env:WGPU_NATIVE          = "$T\wgpu-native"        # build.py's default is a developer's Desktop path
$env:RX_CPM               = "$T\cpm"                # build.py also falls back to tools\cpm; set it anyway (without it hookshot_vk.dll is silently left out)
$env:HOOKSHOT_MINGW       = "$T\llvm-mingw"         # make_game finds it by itself too; set for certainty
# optional: $env:HOOKSHOT_JOBS = "6"                # fewer parallel compiles on a PC short of RAM
Push-Location $T
& "$T\python\python.exe" -I -u -X utf8 nx2\make_game.py `
    --update "C:\Users\ghostdma\Games\HeyListen" `
    --work   "$B" `
    --log    "$B\update-<tag>-<timestamp>.log"
$code = $LASTEXITCODE
Pop-Location
```

Directories that must exist: `<GameFolder>\.hookshot_exefs\` and `<GameFolder>\romfs\`. Without them the script stops
with "This game folder has no copy of the game files to update from". `$B\objcache\` is optional but is the whole point:
without it every file is compiled.

**`--work "$B"` is essential.** On Windows, `make_game.py`'s default `--work` is `%LOCALAPPDATA%\HookshotBuilder` (the
upstream name), not `HeyListenBuilder`. Leaving the flag out would miss `objcache` and compile everything from scratch.

Options that `--update` picks up from the folder by itself: the language (`language.txt`), code mods
(`.hookshot_code_mods`), and FSR (`.hookshot_fsr`). Do **not** pass `--nup`: the .nup is not needed, and the
`--update` path takes precedence anyway.

What is rebuilt and what is reused:

| Step | Update behaviour |
|---|---|
| Read the game | Reused: `.hookshot_exefs` is copied into the work folder; the .nup is not read |
| Translation to C (lift) | **Always redone** (`sa\` was deleted). It is skipped only if `sa\` was kept (`HOOKSHOT_KEEP_BUILD_FILES=1`) and nothing it reads changed (#428) |
| Compile | **Only objects whose key changed**; the rest come from `objcache`. Everything is compiled again if `system_headers.txt` no longer matches, which happens when a new llvm-mingw part ships |
| Link and outputs | Redone: `bin\`, `image.bin`, the launcher, the shader list, `language.txt`, `hookshot_version.txt` |
| Shader preparation | Runs on the GPU at the end, as the GUI does by default (`PrepareLater=false`). Add `--no-prepare-shaders` to leave it to the first game start |
| Afterwards | `objcache` is pruned to the objects this build used, `sa\` and `probe\` are deleted, and `build_times.json` gets a `kind=update` entry |

Expected duration on this PC (the last full build took 28.7 min, 22.3 min of it compiling):

- Small engine-only release: the lift takes roughly 5-10 min (upstream measured 9 min on a 7800X3D), the compile of a
  few files takes 1-5 min, plus linking and shader preparation. **About 10-20 min.**
- Release with a new compiler or system headers: **about 30 min**, the same as a full build.
- Builder download (Phase A): seconds to minutes, depending on how many parts changed (at most about 470 MB).

### GUI automation fallback

Run Phase A1, leave the GUI open on the start page of the game folder it knows from `builder-settings.json`, and press
**Update** (UI Automation: find the button named `Update` and invoke it). Then wait for the text "Your game is updated"
or for `hookshot_version.txt` to show the new `version=`. Use this only if Phase B fails in a way the GUI does not, since
it is fragile against layout changes.

---

## 3. Interface contract: `Update-Game.ps1`

```
Update-Game.ps1 -GameFolder <path> [-DryRun] [-Jobs <int>] [-PrepareShadersLater] [-LogDir <path>]
```

| Parameter | Required | Meaning |
|---|---|---|
| `-GameFolder` | yes | The built game folder. It must contain `hookshot_version.txt`, `.hookshot_exefs\` and `romfs\` |
| `-DryRun` | no | Detect and plan only. **No downloads, no processes started, no writes** (apart from the console) |
| `-Jobs` | no | Sets `HOOKSHOT_JOBS` for the build. The default is `make_game`'s automatic choice |
| `-PrepareShadersLater` | no | Passes `--no-prepare-shaders` |
| `-LogDir` | no | Where the build log goes. Default: `%LOCALAPPDATA%\HeyListenBuilder` |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Up to date: installed ≥ latest. Nothing was done |
| `10` | Updated successfully: `hookshot_version.txt` now shows the latest tag. With `-DryRun`, **10 means "update available, nothing done"** (`status=update-available`) |
| `1` | Error: no version file, network or API failure, a missing `.hookshot_exefs`/`romfs`, the game or builder is running, too little disk space, a failed part check, a non-zero `make_game.py` exit, or a version that did not change after the build |

### Stdout: one record per line, machine-parseable

Every line the script itself prints starts with `HLUPDATE ` followed by `key=value`. A value runs to the end of the
line (paths may contain spaces). The output of `make_game.py` is passed through unchanged, but **only** to the log file
and to stderr, so stdout carries only `HLUPDATE` lines. `make_game.py`'s own `@@` protocol lines (`@@stage <id> <text>`,
`@@times <total> <compile> <previous>`, `@@check ...`) are mapped as follows:

```
HLUPDATE installed=v0.4.0
HLUPDATE latest=v0.4.1
HLUPDATE builder_installed=v0.4.0              # versions\current.txt
HLUPDATE objcache_mb=639                       # 0 => expect a full compile
HLUPDATE plan=download-builder,rebuild         # or: rebuild | none
HLUPDATE status=update-available               # DryRun stops here (exit 10)
HLUPDATE stage=builder-download                # then: builder-ready
HLUPDATE stage=<make_game stage id> <text>     # from @@stage lines: read, lift, compile, link, prepare ...
HLUPDATE times total=1012 compile=301 previous=1721
HLUPDATE log=C:\...\update-v0.4.1-20261116-153000.log
HLUPDATE status=updated                        # exit 10
HLUPDATE status=up-to-date                     # exit 0
HLUPDATE status=error
HLUPDATE error=<one-line reason>               # exit 1, always together with status=error
```

The last `status=` line is authoritative. Exactly one final `status=` line is printed (`update-available` in DryRun,
otherwise `up-to-date`, `updated` or `error`).

### Order of steps

1. Read `hookshot_version.txt`, query `releases/latest`, compare. If installed ≥ latest, exit 0.
2. Preflight checks, all of which fail with exit 1:
   - `bin\HeyListen.exe` (the game) or any `HeyListenBuilder` process is running.
   - `.hookshot_exefs\` or `romfs\` is missing.
   - Less than **15 GB** free on the work drive or the game drive (an estimate covering the lift, objects and new
     outputs; upstream says 60 GB for a first build).
3. If `-DryRun`, print the plan and exit 10.
4. Phase A if `versions\<latest>\installed.txt` is missing.
5. Back up the saves (read-only on the source): if `<GameFolder>\user\` exists, copy it to
   `%LOCALAPPDATA%\HeyListenBuilder\save-backups\update-<from>-to-<to>-<timestamp>\user\`. Upstream advises this before
   an update.
6. Phase B with the environment from §2. After it ends, require exit 0 **and** `version=<latest>` in
   `hookshot_version.txt`, then exit 10.

### What the script must NOT touch

- `<GameFolder>\user\` (the saves): never delete, move or write. It may only be read to make the backup copy.
- `hookshot_options.ini`, `hookshot_env.txt`, `mods\`, `language.txt` (except through `make_game.py` itself), and the
  `.hookshot_*` marker files.
- The shader and pipeline caches (`hookshot_programs.*`, `hookshot_pipelines.bin`, `mkw_vk_pipelines.bin`,
  `hookshot_pipeline_states*`, `hookshot_compiled.bin`, `hookshot_shaders.bin`, `hookshot_prepared.bin`): leave them to
  the build and the game.
- `%LOCALAPPDATA%\HeyListenBuilder\objcache`, `ngx-sdk`, `save-backups`, `builder-settings.json`: the script must never
  delete them. (`make_game.py` prunes `objcache` itself, which is expected.)
- The user's `.nup` file, and older `versions\v*` folders (leave their cleanup to the user or the builder's
  *Free up space*).
- The script must never set `HOOKSHOT_KEEP_BUILD_FILES` unless asked (it keeps about 4 GB around), and never push or
  upload anything.

---

## 4. Risks and unknowns (with evidence)

| # | Risk / unknown | Evidence | Mitigation |
|---|---|---|---|
| 1 | The Windows GUI has no headless `--update` or `--cli` | DLL flag strings are only `--fsr --language --launch --no-prepare-shaders --out --romfs`; `--cli` and `--update <folder>` are documented for the AppImage only | Call `make_game.py --update` directly (Phase B) |
| 2 | A wrong `--work` causes a full recompile | The Windows default is `%LOCALAPPDATA%\HookshotBuilder`; the real cache is in `HeyListenBuilder\objcache` | Always pass `--work "$env:LOCALAPPDATA\HeyListenBuilder"`; print `objcache_mb` first |
| 3 | DLSS is lost silently if `HOOKSHOT_NGX_SDK` is not set | The build has DLSS only when that variable points to the SDK (make_game.py lines 1203-1268, build.py line 217); `builder-settings.json` has `Dlss:true`; `ngx\nvngx_dlss.dll` is present | Set it to `HeyListenBuilder\ngx-sdk`; after the build, check that `ngx\nvngx_dlss.dll` still exists |
| 4 | The Vulkan renderer is left out silently | The readme says that without `RX_CPM` the build leaves out `hookshot_vk.dll`; `WGPU_NATIVE` defaults to a developer's Desktop path | Set both; after the build, check that `bin\hookshot_vk.dll` exists |
| 5 | The environment the GUI sets is not fully known | The DLL names only `HOOKSHOT_JOBS`, `HOOKSHOT_KEEP_BUILD_FILES`, `HOOKSHOT_NO_UPDATE_CHECK` and `HOOKSHOT_UI_PREVIEW`; the other variable names are probably assembled at runtime | Compare a Phase B build log with the GUI's log of the same version once; if outputs differ, fall back to the GUI |
| 6 | A new toolchain means a full compile | `system_headers.txt` stamp deletes `objcache` (lines 750-765); llvm-mingw is a separate part | Expected and correct; report it in the duration estimate |
| 7 | The `heylisten-setup.txt` columns are inferred, not documented | Format seen: `part <sha> <size> <dest> <strip> <rename> <url>`; `parts.txt` lists only 5 of the 8 hashes | Prefer A1 (the official bootstrapper); A2 is a fallback |
| 8 | Bootstrapper automation can hang on SmartScreen or UI prompts | The user guide mentions the SmartScreen "unknown publisher" warning | Poll `installed.txt` with a timeout; on timeout, exit 1 with an error |
| 9 | The upstream docs disagree about update speed | The readme says "about as long as the first build"; v0.4.0's code and GUI say only what changed is compiled | Trust the code (v0.4.0) |
| 10 | Saves | `user\` does not exist yet here; the user guide says updates keep saves | The backup copy in step 5 |
| 11 | The launcher's `--tag`, `--repair` and `--launch` flags are undocumented | Strings found in `HeyListen.exe` | Do not use them |
| 12 | `build_times.json` is not strictly valid for PowerShell | It has keys that differ only by case (`Decode.cpp` / `decode.cpp`), which `ConvertFrom-Json` rejects | Do not parse it; use the `@@times` line |

### Recommended default

`Update-Game.ps1 -GameFolder C:\Users\ghostdma\Games\HeyListen`, with these steps:

1. Detect the update through `releases/latest`.
2. If needed, install the builder with the **official bootstrapper** (A1, waiting for `installed.txt`).
3. Back up `user\`.
4. Run **`make_game.py --update` with `--work %LOCALAPPDATA%\HeyListenBuilder`** and the environment variables from §2
   (`HOOKSHOT_NGX_SDK`, `WGPU_NATIVE`, `RX_CPM`, `HOOKSHOT_MINGW`, `HOOKSHOT_BUILDER_EXE`), with shaders prepared as
   the GUI does.
5. Check `hookshot_version.txt`, `bin\hookshot_vk.dll` and `ngx\nvngx_dlss.dll`.

Use the GUI's **Update** button only as a fallback. Today the result is exit 0, because v0.4.0 is already the latest.

DONE
