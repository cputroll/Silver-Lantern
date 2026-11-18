# Independent containment re-review: v1.7.1

**Verdict: FIX-FIRST**  
Reviewed main `c56517c071788f2d1f0d78852577073534666a0c` on Windows, 2026-11-18. This is a focused containment review, not a full release-gate sign-off.

## Release blocker

**The boundary is not revalidated at every game write.** Version 1 manifests remain accepted (`Get-HeyListen.py:1868`). Their installation path calls `game_path` once, then creates the parent and opens the staging file (`:2556-2562`), and later replaces the final target (`:2583`), without `verify_write_boundary` at either write boundary. A parent swapped for a junction after the initial check can redirect the staging creation or final placement outside the game root. The window before final placement includes the entire assembly, not merely a few microseconds. Add boundary checks immediately before legacy staging open and final replacement, and a real legacy junction-swap regression. Do not describe all supported installation paths as covered until then.

## What was verified

- The segmented worker checks the boundary before staging open, checkpoint creation, and final replacement (`:2251-2252, 2271-2274, 2279-2280`). `_open_staging_checked` rechecks after an open error. The real Windows `JunctionSwapDuringAssemblyTests` changes a live directory to a junction while a second bundle is gated and checks for `InvalidGamePath` and an unchanged outside sentinel.
- Actual separate execution: `python -m unittest test_write_boundary` — **6 tests passed, 0 skipped**. Its prefix test covers extended-length drive paths and extended UNC conversion to ordinary UNC. Combined execution of `test_write_boundary test_invalid_game_path test_game_sync test_game_v2 test_checkpoint_recovery test_launcher_install_protection` reported **59 tests OK, 1 skipped**; PowerShell's stderr wrapper gave the piped command exit status 1 despite unittest reporting OK.
- Exclusive staging creation (`path.open('xb')`) remains in place for both formats and checkpoint temporary files, rejecting pre-existing reparse-point staging names rather than truncating their referents. Append checks the staging name before opening, but the check and open are not atomic.
- The segmented boundary helper documents a residual same-user local race, whereas malicious remote manifest paths are separately validated. That threat-model distinction is reasonable for segmented writes, but the claim that a swapped junction can *never* receive a game file is too strong: checks are pathname-based, not handle-relative, and open/replace and retry operations can race; `_leftover_kind` also treats all stat errors as absent, not just missing paths. The legacy gap above is materially wider than the documented microsecond window. Correct the absolute wording and fail closed on unexpected stat errors; handle-relative operations would be needed for a stronger guarantee against local racers.

This does not assert that a remote package attacker alone can perform the local junction swap. Packaged friend simulation and unrelated prior findings were outside this focused review.
