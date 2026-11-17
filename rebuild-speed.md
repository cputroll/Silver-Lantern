# HeyListen rebuild speed: findings and recommendations

Machine: Ryzen 9 9950X3D (16C/32T), 32 GB RAM (31.2 GB visible), RTX 5080, Windows 11.
Builder: v0.4.0 (`%LOCALAPPDATA%\HeyListenBuilder\versions\v0.4.0`). Game folder: `C:\Users\ghostdma\Games\HeyListen`.
Everything below was checked read-only. Code references are to `...\versions\v0.4.0\HookshotBuilder\tools\nx2\`.

---

## 1. What the build recorded

`%LOCALAPPDATA%\HeyListenBuilder\build_times.json`:

```
"compile_seconds": 1338.7   (22.3 min)
"total_seconds":   1720.6   (28.7 min)
"compiled": 560, "modules": 695, "kind": "build", "when": "2026-11-16 15:21:53"
```

The 22 minutes you measured is `compile_seconds`. The script's own total is 28.7 min. The other ~6 min of your 35 min
happened before `make_game.py` started its clock (`STARTED`, make_game.py:45): downloading and installing the builder,
using the UI, and checking the `.nup`. **No timing record covers that part, so it can't be measured from here.**

No build log was found. The search for `*.log`, `build*.txt` and `*log*.txt` in both folders found nothing. The stage
times below come from file timestamps plus `build_times.json`.

### Per-stage timeline (first build)

| Stage (make_game.py `say("stage", ...)`) | From → to | Duration | Evidence |
|---|---|---|---|
| Start, DLSS SDK fetch | 14:53:12 → 14:53:25 | ~13 s | `STARTED` = 15:21:53 − 1720.6 s ≈ 14:53:12; `builder-settings.json` and `system_headers.txt` 14:53:13; `ngx-sdk\` 14:53:25 |
| **read**: unpack .nup (25.0 GB romfs, 186,587 files) | 14:53:25 → 14:56:09 | ~2 m 45 s | `.hookshot_exefs`, `romfs`, `.hookshot_romfs` all 14:56:09 |
| read: damaged-file check (`stand_in_damaged_files`) | 14:56:09 → 14:56:30 | ~21 s | `.hookshot_romfs_checked` 14:56:30 |
| **lift**: translate to C plus `split_hot.py` | 14:56:30 → ~14:59:26 | **~3 min (derived)** | lift inputs copied at 14:56:30 (`hot_funcs.txt`, `hookshot_pieces.prof`, ...). Compile start = 15:21:45 (last object) − 1338.7 s ≈ 14:59:26 |
| **compile + link** (`nx2/build.py`) | ~14:59:26 → ~15:21:45 | **22.3 min** | `compile_seconds` 1338.7; `objcache` mtimes run 15:01:02 → 15:21:45 |
| **finish**: copy to `bin\`, shader census, launcher, version | 15:21:45 → 15:21:53 | ~8 s | `bin\` 15:21:48; `hookshot_shaders.bin` and `hookshot_version.txt` 15:21:53 |
| *(first game start)* shader preparation | 15:21:53 → ≤15:26:06 | **≤ ~4.2 min** | `hookshot_pipeline_states_shipped.done` 15:26:04; `hookshot_programs.bin`/`.idx` and `hookshot_pipelines.bin` 15:26:06 (an upper bound: the game may not have started at 15:21:53) |
| *(play)* driver pipeline cache, ledger | → 15:28:30 | – | `hookshot_compiled.bin` 15:27:37, `mkw_vk_pipelines.bin` 15:28:30 |

### Inside the 22.3-minute compile

I summed the per-file seconds in `build_times.json` with the bundled Python:

| Group | Files | Sum of per-file seconds | Largest file |
|---|---|---|---|
| `code_hot_0..7.c` (-O2 + PGO profile) | 8 | 2,160.6 s | `code_hot_1.c` 325.7 s |
| other lifted chunks `code_*.c` (-O1) | 71 | 4,352.8 s | `code_021_1.c` 220.3 s |
| runtime / renderer / opus / zstd (C, C++) | 481 | 1,007.1 s | 13.3 s |
| **Total** | **560** | **7,520.5 s** | |

- **Average parallelism = 7,520.5 / 1,338.7 ≈ 5.6 compilers at once** on a 32-thread CPU.
- The CPU lower bound is the longest single file: about 326 s (5.4 min). Total work ÷ 32 threads is about 235 s.
- So the wall time was about **4× the critical path**, and the CPU was mostly idle.
- The `objcache` mtimes per minute show the shape. From 15:01 to 15:13, 2–7 heavy objects finished per minute
  (13–64 MB per minute). From 15:14 to 15:19, the 480 small files finished (13 → 200 per minute). Even the small-file tail
  only reached about 3–5 at once.

**Why parallelism is low (mechanism, build.py:410-451 `MemoryGate`):**
- `HOOKSHOT_JOBS` defaults to `os.cpu_count()` = 32 (make_game.py:550-555), but a compiler only starts when this holds:
  `free RAM − (memory still owed to running compilers) − HOOKSHOT_MEM_RESERVE_GB (1.5) ≥ the file's need`.
- Needs (`mem_need`, build.py:392):
  - a hot piece: 1,500 MB;
  - a lifted chunk: 400 MB + 50 MB per MB of C + 250 MB per MB of its largest function (~1.5–2.2 GB for a 25 MB chunk);
  - a C file: 400 MB; a C++ file: 1,000 MB.
- On Windows at least `min(4, 32 // 2) = 4` compilers always run, whatever the memory.
- Free RAM right now is **9.6 GB of 31.2 GB**, so other programs hold about 21 GB. With about 9 GB free:
  (9 − 1.5) / ~1.6 GB ≈ 4–5 heavy compilers. That is the same as the measured 5.6.
  The free RAM during the build itself was not recorded (no log), so this is **strong circumstantial evidence, not a
  measurement**.

Also: `objcache` holds 695 objects (= `modules`), but only 560 were compiled. The other 135 modules were reused
(identical source and flags, linked out of `objcache`, build.py:682-692).

---

## 2. What each file is: cache or rebuilt?

### Game folder (`C:\Users\ghostdma\Games\HeyListen`, on C: = Samsung 9100 PRO 8 TB NVMe)

| File / folder | Size | What it is | Made again by a rebuild? | Cost if lost |
|---|---|---|---|---|
| `romfs\` | 25,022 MB, 186,587 files | Game data unpacked from the .nup | **No**. Skipped when `.hookshot_romfs` (stamp = .nup file name + byte size) matches (make_game.py:1386-1393) | ~3 min unpack |
| `.hookshot_romfs` | stamp | "unpacked by this builder: <name> <size>" | – | forces re-unpack |
| `.hookshot_romfs_checked` | stamp | Damaged-file check done for this unpack | No (once per unpack, make_game.py:353-356) | ~21 s |
| `.hookshot_exefs\` | 85 MB | Game code as read; what updates build from | Copied into the work folder each build (seconds) | update impossible without the .nup |
| `bin\` | 490 MB | The compiled game and its DLLs | **Yes, every build** (link output) | – |
| `image.bin` | 96 MB | Game image from the lift | Yes, every build | – |
| `hookshot_shaders.bin` | 514 MB | Shader census of romfs (list of all shaders) | **Yes, every build**, finish stage (make_game.py:1644), ~5 s | trivial |
| `hookshot_pipeline_states_shipped.bin` | 5 MB | Shipped state list (`ship_state_list`) | Yes, every build | trivial |
| **`hookshot_programs.bin` + `.idx`** | **6,633 MB** | **Translated shader program cache (SPIR-V)**. Valid only for `HOOKSHOT_SHADER_STAMP` (build.py:890-903: SHA-1 of `gpu/vk/eden`, `sirit`, `compat`, `vk_shader.cpp`, `vk_bank.cpp`, `vk_state.h`, `vk_api.h`) | **No.** The builder never touches it. The game throws it away only when the shader stamp changes, i.e. when a release changes the shader translator | **~4 min re-preparation** at the next start |
| `hookshot_pipelines.bin` | 181.5 MB | Pipeline notes (program + draw state) the precompile builds before the title. It has its own layout stamp, `HOOKSHOT_STATE_STAMP`, and **survives a shader-translator change** (build.py:895-902, "TICK-008") | No | first-visit stutter comes back |
| `mkw_vk_pipelines.bin` | 754 MB | NVIDIA's `VkPipelineCache` (vk_draw.cpp:695) | No (a driver update invalidates it) | pipeline rebuild time at startup / in play |
| `hookshot_compiled.bin` | 0.5 MB | Ledger of pipelines already in the driver cache (vk_draw.cpp ~1700) | No | small |
| `hookshot_prepared.bin`, `hookshot_pipeline_states.bin` | ~0 | Preparation state / states seen in play | No | small |

### Builder folder (`%LOCALAPPDATA%\HeyListenBuilder`, also on the C: NVMe)

| Item | Size | Role |
|---|---|---|
| `objcache\` | **695 `.o`, 638.8 MB** | Content-addressed object cache. Key = SHA-1 of source + flags + dependencies (`compile_one`, build.py:676-710). **This is what makes rebuilds incremental.** After a build it is pruned to the objects that build used (`prune_object_cache`, make_game.py:768) |
| `sa\` (translated C `sa\gen`, `sa\build\obj`) | **absent**, about 1.5 GB when kept | Deleted after every build unless `HOOKSHOT_KEEP_BUILD_FILES=1` (make_game.py:1693-1703) |
| `system_headers.txt` | stamp `0c83f63e…` | clang version + system headers. **If this changes, `objcache` is wiped** (`drop_stale_objects`, make_game.py:824-883) |
| `build_times.json` | 16 KB | Per-file timings, used to order the next build (biggest first) and to estimate its time |
| `hot_funcs.txt`, `hot_order.txt`, `hookshot_pieces.prof`, `hookshot.res`, `image\` | ~15 MB | Lift inputs copied from the tools. **Part of the lift key** |
| `ngx-sdk\` | 26 files, 60.3 MB | DLSS SDK, placed into the game's `ngx\` (56 MB) |
| `versions\v0.4.0\` | 12,966 files, 1,492 MB | The builder itself (llvm-mingw 718 MB, emsdk 286 MB, Python 142 MB, ...) |
| `parts.txt`, `installed.txt` | **not present** | – |

There is also an old `%LOCALAPPDATA%\HookshotBuilder` folder. HeyListen shares nothing with it, and it has no effect on
speed.

---

## 3. What a rebuild actually redoes

| Step | When it is skipped | Mechanism |
|---|---|---|
| Read (.nup unpack, 25 GB) | Same .nup **file name + size** (or `--update`, which uses `.hookshot_exefs` + `romfs`) | make_game.py:1386-1393. **Nothing re-hashes the 25 GB on a rebuild.** Only the 85 MB exefs is copied |
| Damaged-file check | Once per unpack stamp | `.hookshot_romfs_checked` |
| **Lift (~3 min here)** | Only if `sa\gen` still exists **and** the lift key matches. The key covers: the exefs, every `.py/.c/.h/.cpp/.txt/.json` under `nx2\` and `third_party\a64-kit`, the HLE function list, `hot_funcs.txt`, `chunks.txt`, the `A64_/OOT_/RX_` env vars and the Python version (make_game.py:1087-1110) | **With the default settings `sa\` is deleted, so the lift always runs again.** With `HOOKSHOT_KEEP_BUILD_FILES=1` it is skipped when the tools did not change |
| **Compile** | Per **file**, not per function: an object is reused when its source text, flags and dependencies hash the same | A changed function only recompiles its ~25 MB chunk, **if** the lift is deterministic and chunk boundaries don't move. A translator change that touches codegen everywhere recompiles all 79 game files |
| Hot files (8, 2,160 CPU-s, critical path 326 s) | Rebuilt whenever `hookshot_pieces.prof` changes (its SHA-1 is in the flags, build.py:791) or `hot_funcs.txt` changes | A new release with a new profile means at least ~5.5 min wall time, even if nothing else changed |
| Runtime (481 files, 1,007 CPU-s) | "any host/*.h change rebuilds all of it" (build.py:805) | 1–3 min |
| Everything | `objcache` wiped by: a failed compile (make_game.py:1564-1576), a clang or headers change (`system_headers.txt`), or **Free up space** | Full 22 min |

**The docs disagree here.** `engine/readme.md` (Hookshot upstream) says the build files are deleted and "an update takes
about as long as the first build". The v0.4.0 code (issue 1287, make_game.py:1693-1703) and `docs/USER_GUIDE.md` keep
the objects ("updates only recompile what changed"). **The code wins:** `objcache` (639 MB) is present on this machine
after the build.

---

## 4. Changes that shorten rebuilds, ranked

| # | Change | Expected saving | Risk | Mechanism |
|---|---|---|---|---|
| 1 | **Never press "Free up space"** and never delete `objcache` | **Up to ~19–22 min per update** (everything whose chunk did not change is linked in from the cache at 0 s) | None; costs 639 MB on a drive with 1.5 TB free | build.py:682-692 cache hit; prune keeps only current objects, so it doesn't grow |
| 2 | **Free RAM during the build**: close the browser, games, local AI models and anything else holding ~21 GB (free now: 9.6 / 31.2 GB). Aim for ≥ 24 GB free | **Estimate: full compile 22 → ~8–12 min.** Heavy compilers allowed go from ~5 to ~14. The limit then becomes the 326 s hot-file critical path plus SMT contention | None | `MemoryGate.enter` admits compilers by free RAM (build.py:426-439). Measured parallelism 5.6 matches ~9 GB free. *Estimate, not measured: confirm with a logged build* |
| 3 | `HOOKSHOT_KEEP_BUILD_FILES=1` | **~3 min** (the lift) on rebuilds where the tools did not change: rebuild after a failed launch, language change, or a release that only touches `host/`/`gpu/`. Also skips relinking cache objects (the `.sha1` stamp next to `sa\build\obj`, build.py:680) | ~1.5 GB more disk (irrelevant here). Releases that change `nx2\`/a64-kit lift again anyway | make_game.py:1461 `lifted_before`; 1698 the delete is skipped |
| 4 | Keep the work folder and game on **C: (Samsung 9100 PRO NVMe)**, which is already the case. **Do not** move the build-files folder to D: (Samsung 870 **QVO**, SATA QLC) | Avoids a slowdown, no gain: already optimal | – | Hard links (`os.link` cache → obj, romfs) also need the same volume |
| 5 | Leave the shader caches alone: `hookshot_programs.bin` (6.5 GB), `hookshot_pipelines.bin`, `mkw_vk_pipelines.bin` | **~4 min per first start** plus first-visit stutter | None | Shader stamp invalidates programs.bin only when the translator changes. pipelines.bin carries over even then |
| 6 | Defender exclusion for `%LOCALAPPDATA%\HeyListenBuilder` and `C:\Users\ghostdma\Games\HeyListen` (and/or the process `clang-23.exe`) | **Not measured.** Plausible 10–30 % on the 481-file small-file tail (~2 s each, much of it process start + file scan) and on the 186k-file unpack | Lower AV coverage of those folders; only for folders you trust | Each compile is a fresh `clang.exe` → `clang-23.exe` process writing a `.o` |
| 7 | `HOOKSHOT_MEM_RESERVE_GB=0.5` (default 1.5) | Small: about +1 heavy compiler, maybe 3–8 % on the compile | Windows may page under memory pressure; it doesn't fail (build.py:414-416) | build.py:423 |
| 8 | Don't cancel or interrupt a compile, and don't run a second builder against the same folder | Avoids a forced full rebuild (22 min) | – | A failed compile deletes `objcache`, `sa\build\obj` and the lift key (make_game.py:1564-1576) |
| 9 | `HOOKSHOT_JOBS`: **leave it on automatic (unset or 0 → 32)** | 0 | Setting it above 32 does nothing; setting it low only slows the build | Jobs are a ceiling, and RAM is the binding limit (see §1) |
| 10 | Hash-cache for the 25 GB check | **0: already done.** A rebuild never re-reads or re-hashes the romfs: a name+size stamp skips the unpack, and `.hookshot_romfs_checked` skips the damaged-file scan | – | make_game.py:1386-1393, 342-356. Whether the C# front end hashes the .nup when you pick it is **not measurable from here** (no log, closed binary). If it does, it happens before `STARTED` and falls in the unmeasured ~6 min |

Would a tool change help? The biggest structural gain would be finer lift granularity (per-function object keys) or a
deterministic chunk layout across releases. Both need upstream changes. Nothing is configurable today.

---

## 5. Recommended profile for this 16C/32T, 32 GB machine

Set these for your user account, then **restart the HeyListen Builder** so it picks them up:

```powershell
setx HOOKSHOT_KEEP_BUILD_FILES 1
# HOOKSHOT_JOBS: leave unset (automatic = 32, limited by memory)
# optional, only if you build with little else open:
setx HOOKSHOT_MEM_RESERVE_GB 1
```

Before you press Update or Build, close browsers, games and other memory-heavy apps (target ≥ 24 GB free in Task Manager).

| Scenario | Expected time with this profile | Today's default with ~9 GB free |
|---|---|---|
| Rebuild, same release (e.g. after a failed launch or a language change) | **~1–2 min** (no read, no lift, objects reused, link + finish) | ~4–5 min (lift ~3 min + link) |
| Update that changes only `host/`/`gpu/` (runtime) | ~2–4 min (+ ~4 min shader prep if the shader stamp changed) | ~5–8 min |
| Update with a new hot profile or translator (most game code changes) | ~8–12 min (estimate, depends on free RAM) | ~22 min |
| Full first build | ~11–15 min script time (unpack 3 + lift 3 + compile 8–12) | 28.7 min measured |

To measure the next run, use the command line with a log. Use the builder's bundled Python, and set the env vars the
builder normally sets (`RX_CPM`, `WGPU_NATIVE`, see `engine/readme.md` "Windows, from the command line"):
`python -I nx2/make_game.py --update "C:\Users\ghostdma\Games\HeyListen" --log "%TEMP%\hl-build.log"`.
The log prints "up to N compilers", the per-file `@@file` lines and "the build took …". A `--cli` mode is documented only
for the Linux AppImage.

---

## 6. Myths and traps

- **"More jobs = faster."** No. The default is already 32; RAM limits it to ~5 here.
- **"Free up space before updating to keep things clean."** It deletes the 639 MB object cache, and the next update
  costs a full ~22 min compile. Not worth it on a drive with 1.5 TB free.
- **"Delete hookshot_programs.bin (6.5 GB) to fix things or to speed things up."** It costs ~4 min of preparation plus
  stutter. It is invalidated automatically when it is no longer valid.
- **"Updates only re-translate the changed functions."** False. The **whole game is re-lifted** whenever `nx2\`, a64-kit,
  the hot list or chunks change. Reuse works per **file** (79 big game files), not per function.
- **"Each rebuild re-verifies the 25 GB dump."** False. Name+size stamps skip both the unpack and the damaged-file check.
- **Moving the build folder to D:** that drive is a SATA QLC (870 QVO), slower than C: (9100 PRO NVMe).
- **The engine readme's "an update takes as long as the first build"** is stale for v0.4.0. The objects are kept.
- **A cancelled or failed compile** or **a builder update that ships a new clang** silently wipes `objcache`. The next build
  is a full one.

DONE

Summary:
1. Script time 28.7 min: unpack ~3 m, lift ~3 m (derived), compile 22.3 m, finish ~8 s. The other ~6 min of your 35 min is outside the script and not measurable.
2. The compile ran only ~5.6 compilers at once (7,520 CPU-s / 1,339 s): RAM-gated (`MemoryGate`, ~9 GB free), not job-limited. HOOKSHOT_JOBS already defaults to 32.
3. Biggest wins: keep `objcache` (never Free up space), and free ~24 GB of RAM before building (estimate 22 → 8–12 min).
4. `HOOKSHOT_KEEP_BUILD_FILES=1` skips the ~3 min lift when tools are unchanged; a same-release rebuild becomes ~1–2 min.
5. Shader caches: programs.bin (6.5 GB, ~4 min to rebuild) and pipelines.bin are never touched by the builder; only hookshot_shaders.bin (5 s) is remade. Don't delete them.
6. No re-hashing of the 25 GB romfs happens on rebuilds; disk placement (C: NVMe) is already optimal; a failed compile or new clang wipes the cache.
