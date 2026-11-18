# Independent containment re-review: v1.7.1

**Verdict: FIX-FIRST**  
Reviewed main `138111db6ffd4190b0fcf60d66705c6362ae0088` on Windows, 2026-11-18. This is a focused containment review, not a full release-gate sign-off.

## Remaining gap

`_leftover_kind` (`Get-HeyListen.py:770-778`) treats every `os.stat(..., follow_symlinks=False)` error as an absent path, including errors other than a missing path. `verify_write_boundary` uses that result when walking the destination (`:2032-2038`), so it cannot substantiate its claim to fail closed on every existing reparse component when inspection fails. Distinguish missing paths from unexpected stat errors and reject the latter; add a focused regression test. This is an inspection-failure gap, not a demonstrated remote-only escape.

The same helper's docstring (`:2017-2023`) says a swapped junction can **never** receive a game file, even though its checks and subsequent pathname-based open/replace are not atomic. Correct that absolute claim and describe the residual same-user local race without implying a strict timing bound. Handle-relative operations would be needed for a stronger guarantee against a local racer.

## Verified

- The previously missing v1 behavioral regression is present: `test_v1_junction_swap_refused_and_referent_untouched` gates the second fetch, replaces `bin` with a Windows junction mid-install, expects `InvalidGamePath`, verifies the sentinel is unchanged, and asserts that the outside directory contains only that sentinel.
- Fresh execution of `python -m unittest test_write_boundary` exited 0 with the following actual summary:

  ```text
  Ran 8 tests in 0.847s

  OK
  ```

- The earlier combined execution of six suites reported 59 tests OK, 1 skipped; it was not repeated for this update. Packaged friend simulation and unrelated prior findings remain outside this focused review.
