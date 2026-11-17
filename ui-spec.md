# Silver Lantern: Console UX Design Spec (for v1.2.x)

**Scope.** This spec was written from a read-only pass over `Get-HeyListen.py`, `README.md`,
`sources.json`, both `.cmd` wrappers and `Apply-HeyListen-Preset.ps1`. The two audit reports were
mentioned but not included, so the spec works from the brief's summary of them (2/10 for novices,
plus the four named problems: technical error text, a console that closes on error, no disk-space
guidance for a 25 GB download, and no clear "what happens next").

**The file changed while it was being read.** Line numbers moved between reads (for example
`--no-launch` moved from line 526 to 537, and the Builder message from 576 to 587), because
v1.2.0 is in flight. Everything below therefore points to **functions and message text, not line
numbers**.

---

## 0. Problems that need fixing first (B-items)

| # | Problem | Why it matters | Fix |
|---|---|---|---|
| B1 | **`apply_preset()` is called in `main()`, but no definition was found.** A search for `def apply_preset` returned no match. | Unless v1.2.0 adds it, every first run crashes with a `NameError` right after a 25 GB download and hash check. | Confirm v1.2.0 defines it. If not, this blocks the release. |
| B2 | **The preset is applied before the game has been built.** `apply_preset(game_dir)` runs before the Builder is even opened, so on a first run `~\Games\HeyListen` doesn't exist yet. The `.ps1` version also exits with an error when the folder is missing. | The preset can never apply on a first run. | Apply it after the Builder closes (see §5). |
| B3 | **Switching to another download source can produce a corrupted file.** If source 1 (25,020,741,061 bytes) fails partway, its `.part` file stays on disk. Source 2 (25,622,267,860 bytes) then "resumes" onto it with a Range request, because `stream_to` only discards the `.part` when it is *larger* than expected. Source 2 has `sha256: null`, so the mixed file **passes as valid**. | Silent corruption. | Name the `.part` file per source (e.g. `dest.with_name(f"{dest.name}.{size}.part")`), or record source URL and size in `.heylisten-state.json` and delete the `.part` when they don't match. |
| B4 | The final `except` prints `ERROR: {exc}` to stderr and the program exits. | When double-clicked, the window closes before anyone can read it. | §4 and §7. |
| B5 | Download progress goes through `log()` every 2 s. | About 1,800 timestamped lines scroll by for each hour of downloading. | §2. |
| B6 | **QuickEdit mode:** if the user clicks inside the console window, Windows pauses all output from the program. The download freezes until they press a key. | Novices click on windows constantly. The download looks hung - probably the top "it's stuck" report. | Turn off `ENABLE_QUICK_EDIT_MODE` during the run and restore it at exit (§6). |
| B7 | If someone double-clicks the EXE **inside the zip** in Explorer, it runs from a temp folder without `sources.json`. You get "no usable manifest" or, worse, the bundled copy is skipped. | The most common mistake novices make with zips. | Detect it at startup (§1). |
| B8 | The full 25 GB SHA-256 check runs again on **every** run, even when the file was already verified. | Several wasted minutes each time someone re-runs to "make sure". | Store `{size, mtime_ns, sha256}` in the state file after verifying. Skip the check when size and mtime match. |
| B9 | Size units don't match. Messages say "25 GB", but `human()` uses 1024-based units, so the file shows as "23.3 GB". | Confusing, though `human()` does match what Explorer shows. | Keep `human()`. Delete hard-coded "25 GB" text and compute sizes with `human(size)`. |
| B10 | Renaming files after download (`part.replace(dest)`, `tmp.replace(builder)`) fails immediately with `PermissionError` (WinError 32 or 5) while antivirus is still scanning the file. | Most likely on the freshly downloaded `HeyListenBuilder.exe`. | Retry the rename for up to ~15 s with backoff before failing (§4). |
| B11 | Before the program even starts, SmartScreen shows "Windows protected your PC", and the PyInstaller onefile loader shows a blank window for 2-5 s. | This is the first decision the user faces, and our code can't touch it. | The README must explain it (§7). |

---

## 1. Startup

**Order in `main()`:**

1. Parse arguments.
2. For `--version` or `--print-sources`: print and exit **with no header**. `test_release.py`
   checks that `stdout.strip() == version`.
3. `ui_init()`.
4. Header.
5. Update check.
6. Manifest.
7. Disk-space check.
8. Builder.
9. Game.
10. Verify.
11. Finish.

Today the manifest loads before the update check. Swap them, so that a freshly updated EXE reads
its own bundled `sources.json`.

```
  Silver Lantern 1.2.0
  Downloads the game data and the HeyListen Builder, then opens the Builder.
  ----------------------------------------------------------------------

  Checking for updates............ up to date
  Download links.................. up to date
  Free space on C:................ 212.4 GB free, 23.3 GB needed
```

- **Step lines** use a fixed-width label padded with dots to column 34, followed by a status.
  `step(label)` writes without a newline and flushes; `done(status, tone)` finishes the line. If an
  inner line has to print before `done()`, the step first ends its own line.
- **Update check:**
  - Up to date: `up to date` (green).
  - Network failure: `skipped (no connection)` (dim, not red).
  - Any other failure: `skipped` (dim). Details go to the log only.
  - Update found: `updating to 1.2.1, restarting...`, then a blank line. The relaunched process
    prints the header again.
  - Not frozen (dev mode): print nothing on screen, log only.
- **Manifest:**
  - Remote URL worked: `up to date`.
  - Fell back to `sources.json` or the cache: `using saved copy` (dim). This is not a warning.
  - Every candidate failed: `UserError("This tool's files are incomplete.", "Extract the whole zip
    into a folder again and run HeyListen-Get.exe from there.")`.
  - Each candidate's URL and exception go to the log file only.
- **Zip detection (B7):** if `sources.json` is missing, `HERE` is under `%TEMP%`, and the path
  contains `\Temp\` plus something like `.zip` or `Rar$`, stop with:
  *"It looks like this was opened straight from the zip. Right-click the zip, choose Extract All,
  then run HeyListen-Get.exe from the extracted folder."*
- **Window title:** `SetConsoleTitleW("Silver Lantern")`. During downloads, update it to
  `Silver Lantern - 63% downloaded` so progress shows on the taskbar when the window is minimized.

## 2. Download

```
  HeyListen Builder............... downloading 38.2 MB
  [################--------------]  54%  20.6 / 38.2 MB  11.3 MB/s  0:02 left
  HeyListen Builder............... ready (v0.9.4)

  Game data....................... downloading 23.3 GB (copy 1 of 3)
  Resuming at 12.4 GB (53%)
  [################--------------]  53%  12.4 / 23.3 GB  41.7 MB/s  4:28 left
```

- **Progress bar:**
  - One line, rewritten in place with `\r` and the bar text, then `\x1b[K` to clear the rest of
    the line when ANSI works. Without ANSI, pad with spaces to the previous line length.
  - **Keep it to `terminal_width - 1` characters or fewer.** If it wraps, `\r` only returns to the
    start of the last row and the line starts repeating. Drop fields in this order as the window
    narrows: bar, then speed, then size.
  - Redraw at most every 0.25 s, and always `flush()`.
  - Speed is a moving average over the last ~10 s (exponential smoothing, alpha ~ 0.2).
    ETA = remaining bytes / smoothed speed, shown as `h:mm:ss` or `m:ss`. Show `--:--` for the
    first 3 s.
  - On completion, end the line with `\n`, or erase it and let `done()` print `ready`.
- **No terminal (output redirected):** no `\r`. Print one plain line every 10%.
- **Retries** show on the same line without scrolling: `connection dropped, retrying (2 of 6)...`.
  The bar comes back on the next chunk. Short reads say nothing on screen (log only).
- **Resume:** when `have > 0` and the server returned 206, print `Resuming at {human(have)} ({pct}%)`
  once, above the bar. If the server ignored Range (returned 200 after a resume attempt), print
  `Starting over (the site didn't allow resuming)` in yellow.
- **Switching sources:**
  `  Copy 1 didn't work (the download site is busy). Trying copy 2 of 3...`
  The reason in brackets comes from `friendly(exc).short` (§4). Never show URLs on screen. Use the
  word "copy", not "mirror" or "source".
- **Multi-part downloads:** one combined bar across all parts (total = manifest `size`), not one bar
  per part. Pass `base` and `total` offsets to the progress object.
- **Disk-space check:** in `download_game`, before the source loop.
  `need = size - existing_part + 1 GiB`, plus the size again for multi-part sources because of the
  `.join` copy. Use `shutil.disk_usage(out)`.
  - Shortfall: stop with *"Not enough free space on C:. The download needs 23.3 GB, and 18.2 GB is
    free. Free up about 6 GB (empty the Recycle Bin, or delete old files from Downloads), then run
    this again."*
  - Always show a dim note: `(The built game needs more space later; keep extra room free.)` The
    Builder's output size is unknown; fill in a real figure once someone measures it.
- **Keep the PC awake** during downloading and verifying:
  `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`, cleared in `finally`. Otherwise a
  25 GB download on a laptop gets cut off by sleep.

## 3. Verification

```
  Checking the download........... 41% (a few minutes)
  Checking the download........... OK
```

- Add an optional `progress=None` callback to `sha256(p)`, called with `(bytes_done, total)` after
  each 8 MB block. Show the percentage in place on the step line, at most 4 times a second.
- A file that matches the pinned hash shows `OK`. A repack with no pinned hash also shows `OK` on
  screen; the computed hash goes to the log only.
- Mismatch: delete the file and stop. Resuming can't repair it. *"The download was damaged and has
  been deleted. Run this again to download a fresh copy."*
- Cached result (B8): `Checking the download........... OK (checked before)`.

## 4. Errors

**Format:** a blank line, a red `Problem:` with one sentence, a blank line, then `What to do:` with
one sentence, a dim `Details were saved to: <log path>`, and finally the pause (§7).

```
  Problem: The internet connection was lost during the download.

  What to do: Check your connection and run this again. It picks up where it left off
  (12.4 GB is already saved).

  Details were saved to: C:\Users\You\Desktop\Silver Lantern\HeyListen-Get.log

  Press Enter to close this window.
```

**Code structure:**

- `class UserError(Exception)` with `(problem, action)` (plus optional `short` and
  `resumable_bytes`).
- `friendly(exc) -> UserError` turns raw exceptions into messages.
- The top-level handler logs the full traceback to the file and shows only `friendly(...)`.

### Error table

| Exception matched | Short form (for "Copy 1 didn't work (...)") | Problem / What to do |
|---|---|---|
| `OSError` with `winerror` 112 or 39, or `errno.ENOSPC` | disk full | "The disk ran out of space." / "Free up space and run this again. It resumes." |
| `PermissionError`, or `winerror` 32 or 5, on a path in `out` | file in use | "Windows wouldn't let this tool use `<name>`. Usually antivirus is still scanning it." / "Wait a minute and run this again. If it keeps happening, allow HeyListen-Get.exe in your antivirus." |
| curl_cffi `ConnectionError`/`Timeout`, or curl error code 6, 7, 28, 35, 52 or 56 | no connection | "The internet connection was lost (or isn't working)." / "Check your connection and run this again. It resumes where it left off." |
| HTTP 403, 404 or 410, or "no download link on" | link no longer works | (only if every copy fails) "All download links are currently unavailable." / "Try again later. The links update automatically once the tool's author fixes them." |
| HTTP 429 or 5xx | the site is busy | "The download site is busy right now." / "Wait about 10 minutes and run this again." |
| Builder checksum mismatch, or a missing asset | - | "The HeyListen Builder download didn't check out." / "Run this again. If it repeats, tell the person who sent you this tool." |
| Game checksum mismatch | - | see §3 |
| `KeyboardInterrupt` | - | dim `Stopped. Run this again any time. It picks up where it left off.`, then the pause. Exit code 130. |
| anything else | - | "Something unexpected went wrong." / "Run this again. If it repeats, send the log file to the person who gave you this tool." |

The "it resumes" wording only appears when a `.part` file exists. Pass a `resumable_bytes` hint in
the `UserError`.

**Renames during antivirus scans (B10):** add `_replace_with_retry(src, dst)`, which retries
`PermissionError` with waits of 0.5 -> 1 -> 2 s for up to 15 s. Use it for `part.replace(dest)`,
`tmp.replace(builder)` and `tmp.replace(dest)`.

**Log file:**

- Location: `HERE / "HeyListen-Get.log"`. If that folder isn't writable, fall back to
  `%LOCALAPPDATA%\SilverLantern\HeyListen-Get.log`.
- Encoding UTF-8, append mode.
- Each run starts with a line like `===== 2026-11-16 14:02:11 v1.2.0 argv=[...] =====`.
- Trim to the last ~2 MB at startup.
- Every line gets an `[HH:MM:SS]` timestamp. Full tracebacks go here (`traceback.format_exc()`).

## 5. The finish

```
  ----------------------------------------------------------------------
  Everything is downloaded. The HeyListen Builder is opening now.

  Your files are in your Downloads folder:
    silver-lantern-data.nup   (the game data, 23.3 GB)
    HeyListenBuilder.exe      (the Builder)

  Next steps, in the Builder window:
    1. Choose the game file: silver-lantern-data.nup in your Downloads folder.
    2. Tick the box "NVIDIA DLSS".
    3. Press "Build the game" and wait for it to finish.

  Leave this window open. When you close the Builder, the graphics settings
  are applied for you.
```

- Show the real path (`out`) if it isn't Downloads. Show sizes from `human()`.
- **Labels need checking against the actual Builder:** the file-picker button text in step 1, and
  whether "NVIDIA DLSS" and "Build the game" are the exact wording. The current code says
  `pick the .nup, tick NVIDIA DLSS, press "Build the game"`. Copy the on-screen text exactly.
- **Fix for B2:** start the Builder with `p = subprocess.Popen(...)`, show the block above, then
  `p.wait()`.
  - When it closes and `game_dir` exists, call `apply_preset(game_dir)` and print
    `Graphics settings............... applied (DLSS Performance)`, then `All done. Enjoy!`.
  - If `game_dir` doesn't exist, print in dim text: `Graphics settings weren't applied because the
    game wasn't built in <game_dir>. That's fine; you can change them in the game's menu.`
  - Then pause.
  - With `--no-launch`, skip the wait.
- **Assumption to check:** that the Builder's default output folder is `~\Games\HeyListen`.
- If the game was already downloaded and verified, the same block appears, so a re-run is a no-op
  that just reopens the Builder.

## 6. Colors and text

- **ANSI detection** in `ui_init()`:
  - Off when `NO_COLOR` is set, `--plain` is passed, or `sys.stdout.isatty()` is false.
  - On Windows, call `GetConsoleMode` / `SetConsoleMode(h, mode | ENABLE_VIRTUAL_TERMINAL_PROCESSING
    (0x4))`. Colors are on only if that call succeeds. Older conhost doesn't fail cleanly; it prints
    `←[32m` garbage.
  - Windows Terminal has VT on already, and the same call simply succeeds.
- **QuickEdit (B6):** save the original input mode from `GetStdHandle(-10)`. Set
  `(mode | ENABLE_EXTENDED_FLAGS 0x80) & ~ENABLE_QUICK_EDIT_MODE 0x40`. Restore it with `atexit`,
  and also explicitly before the final pause so the user can copy the error text.
- **Palette:**

  | Color | Used for |
  |---|---|
  | green | `OK`, `up to date`, `ready` |
  | yellow | warnings and "trying copy 2" |
  | red | only the word `Problem:` |
  | bold | the title |
  | dim (`\x1b[2m`) | notes and the log path |
  | default | everything else |

  No background colors. No blinking. Readability must not depend on color.
- **ASCII only on screen:** `-` rules, `#`/`-` bar, `...` (not `…`), `-` (not `—`), straight quotes.
  - The current strings use `…`, `—` and curly quotes (`“Build the game”`).
  - Python 3.13 writes to a real console through `WriteConsoleW`, so those survive in conhost.
  - But redirected output (a wrapper piping, or `> file`) uses the ANSI code page. Box-drawing and
    block characters (`█░═`) aren't in cp1252 and raise `UnicodeEncodeError`, and the older Raster
    Fonts setting can't render them.
  - As a safety net, call `sys.stdout.reconfigure(errors="replace")` in `ui_init()`.
- **No emoji. No timestamps on screen** (timestamps live only in the log file). Two-space left
  margin. Content width <= 78 so it fits the classic 80-column window.

## 7. Double-click behavior and the wrappers

- **Always pause at the end** when `sys.stdin.isatty()` and `--no-pause` isn't passed:
  `input("\n  Press Enter to close this window.")`, wrapped in
  `try/except (EOFError, KeyboardInterrupt)`. This applies after success (§5) and after errors (§4).
- Detecting "launched by double-click" with `GetConsoleProcessList` is **unreliable under
  PyInstaller onefile**. The bootloader and the Python child both attach to the console, so a
  double-click reports 2 processes, not 1. Pausing whenever stdin is a terminal is the robust rule.
  Scripts can pass `--no-pause`.
- **After a self-update:** the old process exits and the helper relaunches the new EXE in the same
  console, so the new process does the pause.
  - From a double-click this looks seamless.
  - From a cmd prompt, the prompt comes back while the new EXE is still printing. That's
    acceptable; it's only seen from a command line. Document it.
  - Pass `--no-pause` through only if it was given.
- **`SilverLantern-Get.cmd`:** **remove it from the zip.** The EXE now sets its own title and pauses
  itself. The wrapper adds a second "which one do I click?" choice, and its `if errorlevel 1 pause`
  would pause twice.
- **`SilverLantern-Preset.cmd` and `Apply-HeyListen-Preset.ps1`:** **remove them from the novice
  zip**, since §5 applies the preset automatically. If a manual path is wanted, add
  `HeyListen-Get.exe --preset-only [--game-dir X]` instead of a PowerShell script with
  `-ExecutionPolicy Bypass`.
- **Zip contents:** `HeyListen-Get.exe`, `sources.json`, and `README.txt` (plain text, so it opens
  in Notepad). README.txt should contain:
  1. Right-click the zip, choose Extract All.
  2. Double-click HeyListen-Get.exe.
  3. If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**.
  4. You need about 30 GB free. It takes a while; you can leave it running.

  Update `release.ps1` and `test_release.py` to match.
- Keep the EXE name. Self-update depends on `target.name` and on `stage_executable` matching
  `HeyListen-Get.exe`.

## 8. Implementation notes (designed to merge cleanly on top of v1.2.0)

**Strategy:** keep `log()`'s signature, but make it **write to the log file only**. All existing
`log(...)` calls stay as the technical record and need no edits. Add a separate `ui` layer and
insert a few calls to it. Nearly every change is an addition or a new optional parameter, which
keeps merge conflicts small.

1. **New block** `# ---- ui` placed right after `human()`:
   - `LOG_PATH`
   - `ui_init(plain)`: VT mode, QuickEdit, title, `reconfigure(errors="replace")`, opening the log,
     the zip-temp check
   - `say(text, tone=None)`
   - `step(label)` / `done(status, tone)`
   - `warn(text)`
   - `class Progress(label, total, base=0)` with `.update(done)`, `.note(text)` and `.finish()`
   - `set_title()`
   - `pause()`
   - `class UserError`
   - `friendly(exc)`
   - `_replace_with_retry()`
   - `keep_awake()` as a context manager
2. **`log()`:** the body becomes "append `[HH:MM:SS] msg` to the log file". If the file can't be
   opened, swallow the error. Add an optional `echo=False` parameter for debugging.
3. **`sha256(p, progress=None)`:** add the optional callback. Default behavior doesn't change, so
   the tests are unaffected.
4. **`stream_to(..., progress=None, quiet=False)`:** new optional keyword arguments. The tests mock
   `stream_to` by name, so the signature must remain compatible.
   - Replace the inner `if now - last >= 2: ... log(...)` block with `progress.update(done)`, and
     keep a `log()` line every 60 s.
   - On a retry, add `progress.note(...)`.
   - On resume, show the "Resuming at" message.
   - Use `_replace_with_retry` for the final rename.
   - `check_self_update` passes `quiet=True`.
5. **`download_builder`:** add `step`/`done` around the existing logic, create a `Progress` for
   `stream_to`, and use `_replace_with_retry`.
6. **`download_game`:** add the disk-space check before the loop, per-source `.part` naming (B3),
   the "Copy N of M" messages, a combined progress bar for multi-part sources, a verify step with
   progress, the hash cache (B8), and `UserError` on a mismatch.
7. **`load_manifest`:** no edits inside. Wrap the call in `main()` with `step`/`done`. To know
   whether the local fallback was used, add a module flag `_MANIFEST_ORIGIN` set just before
   `return data` - a one-line addition.
8. **`check_self_update`:** add `step("Checking for updates")` and the final `done(...)` in
   `main()`. Before `sys.exit(0)`, add `done("updating to X, restarting...")`.
9. **`main()`:** add `--no-pause` and `--plain`. Call `ui_init` after the `--version` /
   `--print-sources` exits. Reorder so the update check runs before the manifest. Add the header,
   the §5 finish block, and the Builder `wait()` followed by the preset.
10. **The `__main__` handler:** route everything through `friendly()`, write the traceback to the
    log, call `pause()`, and keep exit codes 0, 1 and 130.

### Every existing on-screen message and what replaces it

The `log()` call itself stays (file-only) unless noted.

| Function | Current message | New on-screen behavior |
|---|---|---|
| `load_manifest` | `"  local manifest metadata unusable: {e}"` | none |
| `load_manifest` | `"  manifest cache not updated: {e}"` | none |
| `load_manifest` | `"Manifest: {label} (v...)"` | `done("up to date")` |
| `load_manifest` | `"  manifest {label} unusable: {e}"` | none, then `done("using saved copy")` |
| `load_manifest` | `raise RuntimeError("no usable manifest ...")` | `UserError` (§1) |
| `download_builder` | `"Builder already current: {tag}"` | `done("up to date")` |
| `download_builder` | `"Downloading {asset_name} ({tag}, {size})"` | `step` + `Progress` |
| `download_builder` | `"Builder ready and checksum-verified: {tag}"` | `done("ready ({tag})")` |
| `download_builder` | `raise RuntimeError("{repo} {tag} has no ...")`, `ValueError("missing or ambiguous builder checksum")`, `RuntimeError("builder checksum mismatch ...")` | Builder `UserError` |
| `stream_to` | progress `log(f"  {pct} ...")` | `Progress.update` (log every 60 s) |
| `stream_to` | `"  short read ..., resuming..."` | none |
| `stream_to` | `"  network hiccup (...), retry ..."` | `progress.note("connection dropped, retrying (n of 6)...")` |
| `stream_to` | `raise RuntimeError("size mismatch ...")` | `friendly` -> damaged-download message |
| `download_game` | `"Game already downloaded: ..."` | `done("already downloaded")` |
| `download_game` | `"Game source: {url} -> {size}"` | `step("Game data")` + `done("downloading {size} (copy i of n)")` |
| `download_game` | `"  part {i}/{n} done ..."` | none |
| `download_game` | `"  source failed: {e}"` | `warn("Copy i didn't work ({short}). Trying copy i+1 of n...")` |
| `download_game` | `"Game downloaded: {dest}"` | handled by the finish block |
| `download_game` | `"Verifying SHA-256 (a few minutes on 25 GB)..."` | verify step (§3) |
| `download_game` | `raise RuntimeError("game checksum mismatch ...")` | delete the file + `UserError` |
| `download_game` | `"Checksum OK - ..."` / `"Checksum (re-pack ...)"` | `done("OK")` |
| `replace_transactionally` | `"Update installed; backup retained ..."` | none |
| `check_self_update` | `"Self-update is available only for the packaged executable."` | none |
| `check_self_update` | `"Self-update: up to date ({v})"` | `done("up to date")` |
| `main` | `"Manifest v... - game: ..."` | none |
| `main` | `"Self-update failed safely: {e}"` | `done("skipped...", dim)` |
| `main` | `"Self-update check complete."` | `say` (only with the `--self-update` flag) |
| `main` | `"All set."` | the §5 finish block |
| `main` | `"Opening HeyListenBuilder - pick the .nup ..."` | the §5 finish block |
| `__main__` | `print("\nInterrupted - run again to resume.")` | §4 `KeyboardInterrupt` + pause |
| `__main__` | `print(f"ERROR: {exc}", file=sys.stderr)` | `friendly()` + log the traceback + pause |
| import-time | `sys.exit("Missing dependency ...")` | unchanged (only reachable when running from source) |

`apply_preset` wasn't visible during the read, so its messages need the same treatment once v1.2.0
lands: technical lines go to `log()`, and the screen gets one `step("Graphics settings")` /
`done("applied (DLSS Performance)")` line, or the dim "weren't applied" note from §5.

### Merge order to keep conflicts small

1. Add the new `# ---- ui` block after `human()`. This touches nothing that exists.
2. Change the body of `log()`. Two lines change.
3. Add the optional parameters to `sha256` and `stream_to`.
4. Add the calls in `download_builder` and `download_game`. This is the most likely conflict area.
5. Change `main()` and the `__main__` handler last. This is the other likely conflict area, so
   rebase it after v1.2.0 is final.

### Tests to add (stdlib `unittest`, like the existing ones)

- `friendly()` maps each row of the §4 table correctly. Build each case with a fake
  `OSError(winerror=...)` and a fake HTTP status.
- `Progress` output never goes past `width - 1` columns, at widths 40, 80 and 120.
- `--version` output stays exactly the version string (`test_release.py` already checks this; keep
  it passing).
- The disk-space check raises `UserError` when `shutil.disk_usage` is mocked low.
- A `.part` file from one source is never resumed by a source of a different size (B3).
- `pause()` doesn't block when stdin isn't a terminal, and `--no-pause` turns it off.

---

## 9. Things in the current code that work against this design

| # | Where | Problem | Fix |
|---|---|---|---|
| F1 | `log()` uses `print(..., flush=True)` | Flushing works, but every line ends in `\n`, so nothing can be rewritten in place. A progress line written through `log()` can't share a row with `\r`. Mixing `print` (stdout) with `sys.stderr` in the error path can also print lines out of order. | All screen output goes through `say`/`step`/`Progress`, which write to `sys.stdout` and flush. Errors also go to stdout, not stderr. Call `sys.stdout.reconfigure(line_buffering=False)` once and flush explicitly. |
| F2 | `__main__` prints `ERROR:` to `sys.stderr` | Goes to a different stream, has no pause, and the window closes. | §4 and §7. |
| F3 | Text contains `…`, `—` and `“ ”` | These raise `UnicodeEncodeError` (cp1252/cp437) when output is redirected or piped by a wrapper, turning a cosmetic problem into a crash on the error path. | ASCII only, plus `reconfigure(errors="replace")`. |
| F4 | No VT enablement | Any ANSI code shows up as `←[32m` in classic conhost. | Call `SetConsoleMode(... \| 0x4)` and only turn colors on if it succeeds (§6). |
| F5 | QuickEdit is left on | Clicking the window blocks the `write()` calls, which stalls the whole download loop. The download itself stops, not just the display. | §6 (B6). |
| F6 | `stream_to` reports progress by time (`now - last >= 2`) through `log` | This is where the spam comes from, and the rate is a lifetime average that never reacts to slowdowns. | `Progress` with a smoothed speed (§2). |
| F7 | `stream_to` catches every `Exception` and retries 6 times, 3 s apart | A full disk or a locked file gets retried 6 times (about 18 s of "network hiccup") before it fails, and the message is wrong. | Retry only network errors and HTTP 5xx/429. Raise `OSError`s like ENOSPC / winerror 112/32/5 immediately so `friendly()` can classify them. |
| F8 | `download_game` calls `dest.unlink(missing_ok=True)` on a source failure, but leaves the `.part` | Combined with B3, the next source resumes onto the wrong bytes. | Per-source `.part` names (B3). |
| F9 | `check_self_update` reuses `stream_to` with no `quiet` flag | Once `stream_to` draws a bar, the update download would show a second bar inside the "Checking for updates..." step line. | `quiet=True`, or a small inline bar labelled "Updating". |
| F10 | `check_self_update` calls `sys.exit(0)` from inside the function | Skips the finish block and the pause. Correct here, because the relaunched EXE pauses, but the top-level handler must not treat `SystemExit` as an error or pause on it. | Catch only `Exception` and `KeyboardInterrupt` at the top level (already the case). Keep it that way. |
| F11 | `_apply_update` relaunches with `subprocess.Popen` and no console flags | The child inherits the console, which is what we want. Adding `CREATE_NEW_CONSOLE` would open a second window. | Leave it as it is, and add a comment so nobody "fixes" it. |
| F12 | `main()` loads the manifest before the update check, and prints `Manifest v... - game: ...` | Technical noise on line 1, and the order is wrong (§1). | Reorder the two steps and log only. |
| F13 | `main()` opens the Builder with `Popen` and returns straight away | The window closes (or the prompt returns) while the user still needs the instructions. | `wait()`, then the preset, then `pause()` (§5). |
| F14 | PyInstaller onefile | A 2-5 s blank window before any output appears, and two console processes (which breaks double-click detection). | Pause whenever stdin is a terminal (§7). Consider `--onedir` or `runtime_tmpdir` later. Excluding unused modules shrinks the bundle and shortens the delay. |
| F15 | `human()` uses 1024-based units, while the text says "25 GB" | The numbers on screen disagree. | Compute all sizes with `human()` (B9). |
