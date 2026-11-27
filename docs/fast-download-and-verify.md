# Fast download & fast verification — how the launcher does it

Handoff for developers working on the HeyListen / Longshot launcher
(`Get-HeyListen.py` + `install_scan_windows.py` + `pack-game.py`). Line numbers
refer to the v1.8.1 source snapshot in `D:\heylisten-v043-stage\v181-final-caption-01\src`.

## The numbers (NVMe test install, 186,632 files)

| Path | Time | Note |
|---|---|---|
| Native verifier (32 workers) | **83.675 s** | full SHA-256, identity-bound |
| Legacy fallback verifier | 303.599 s | same keep verdict |
| Fast-path preparation (planning) | ~4.6 s | from 70.7 s before the native planner |

Receipts: `planner-native-timing-32.log`, `legacy-fallback-nvme-timing.log`
(in `D:\heylisten-v043-stage`).

## The GitHub speed hack: parallel HTTP range requests

There is no authenticated API and no special CDN host. The launcher downloads
release assets from the ordinary public URL
(`https://github.com/<repo>/releases/download/<tag>/<asset>`,
`Get-HeyListen.py:1862-1870, 2832-2835`) and makes *one asset* fast by issuing
**disjoint byte-range requests concurrently**:

- `Range: bytes=<start>-<end-1>` per worker (`Get-HeyListen.py:1253`);
- requires HTTP **206** with the exact `Content-Range`, seeks to the offset and
  writes each piece into place (`:1256-1267`);
- the first range is consumed on the main thread while pool workers fetch the
  rest (`:1334-1338`);
- defaults: **6 parallel streams**, enabled for assets **≥ 64 MiB**
  (`:1167-1173, 2833`);
- if the server answers **200** (ranges unsupported, e.g. some proxies/CDNs),
  it discards the partial work and restarts as a single stream (`:1311-1325`).

Why this matters: GitHub's plain asset download is throughput-limited per
connection; six concurrent ranges multiplies the per-connection ceiling.
That is the whole "hack" — standard HTTP range semantics against the public
release URL.

## Download pipeline

- Every asset lands in `.heylisten-parts/` next to the game; a partially
  downloaded asset is **resumable**: completed ranges are re-hashed before
  reuse (`Get-HeyListen.py:1202-1224`).
- An assembled asset is accepted only if its SHA-256 matches the manifest
  (`:2719, 2843-2846`). Nothing is trusted by size alone.
- The packer (`pack-game.py:97-135`) splits the game tree into **tar bundle
  assets of at most 1.5 GiB** (8 MiB I/O blocks; files larger than the limit
  span raw chunks). Many mid-sized assets are what lets the range trick and
  parallel per-asset downloads saturate the link; updates fetch only the
  assets that cover files still pending — they are not binary diffs.
- A single monotonic progress bar tracks all phases; 100% is reserved for
  "Ready to play".

## Fast verification (the 84-second check)

`install_scan_windows.py` + `plan_game_files` (`Get-HeyListen.py:2612-2665`
and the native module):

1. **Inventory filter.** A cheap directory walk (sizes only) narrows the set
   of files that could possibly have changed.
2. **Identity-bound hashing.** For matching-size files, the native planner
   hashes in **256-file batches** with a worker pool; file identity is bound
   (path + size + recorded manifest identity), so untouched files are skipped
   without hashing.
3. **Fail-closed fallback.** A second inventory detects changes the fast pass
   could have missed; any uncertainty falls back to the legacy per-file
   verifier — never a hash skip.

## Operations notes

- The game version stamp is `longshot_version.txt` at the game root
  (legacy installs: `hookshot_version.txt`; both are read).
- Publishing: `AutoShip-Game.ps1` validates the version stamp, uploads the
  immutable assets, **re-downloads and hashes them from GitHub**, then
  advances `game-channel:channel.json` (the pointer carries the manifest
  SHA-256) with a lease and crash-recovery marker.
- Client side: the launcher polls the release feed for the highest published
  stable `vX.Y.Z` (not `/latest`) for its own self-update, and reads
  `channel.json` for game updates; the manifest's SHA-256 is verified before
  parsing.
