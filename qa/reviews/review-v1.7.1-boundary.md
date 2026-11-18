# Independent containment re-review: v1.7.1

**Verdict: FIX-FIRST**  
Rechecked main `023a3aff018593c54d68e963e8e0a9714b47b66b` on Windows, 2026-11-18. This is a focused containment review, not a full release-gate sign-off.

## Remaining release blocker

The legacy v1 path now calls `verify_write_boundary` immediately before staging creation (`Get-HeyListen.py:2562-2563`) and final replacement (`:2584-2585`), closing the previously identified missing-check gap. However, `V1LegacyBoundaryWiringTests` only records and counts two calls during a successful install; it does not perform a real junction swap during v1 assembly or demonstrate rejection and an unchanged outside sentinel. Add that v1 behavioral regression before claiming the legacy path has equivalent swap coverage. Pathname checks remain subject to the local races noted below.

## What was verified

- The segmented worker checks the boundary before staging open, checkpoint creation, and final replacement (`:2251-2252, 2271-2274, 2279-2280`). `_open_staging_checked` rechecks after an open error. The real Windows `JunctionSwapDuringAssemblyTests` changes a live directory to a junction while a second bundle is gated and checks for `InvalidGamePath` and an unchanged outside sentinel.
- Fresh execution: `python -m unittest test_write_boundary test_invalid_game_path` exited 0 and reported:

  ```text
  Ran 14 tests in 0.659s

  OK (skipped=1)
  ```

  The v1 wiring test checks two calls, not their timing or behavior under a junction swap. The prior combined execution of six suites reported 59 tests OK, 1 skipped; it was not repeated for this update.
- Exclusive staging creation (`path.open('xb')`) remains in place for both formats and checkpoint temporary files, rejecting pre-existing reparse-point staging names rather than truncating their referents. Append checks the staging name before opening, but the check and open are not atomic.
- The segmented boundary helper documents a residual same-user local race, whereas malicious remote manifest paths are separately validated. That threat-model distinction is reasonable for segmented writes, but the claim that a swapped junction can *never* receive a game file is too strong: checks are pathname-based, not handle-relative, and open/replace and retry operations can race; `_leftover_kind` also treats all stat errors as absent, not just missing paths. The legacy checks now narrow the previously identified long unchecked interval, but do not make pathname operations atomic. Correct the absolute wording and fail closed on unexpected stat errors; handle-relative operations would be needed for a stronger guarantee against local racers.

This does not assert that a remote package attacker alone can perform the local junction swap. Packaged friend simulation and unrelated prior findings were outside this focused review.
