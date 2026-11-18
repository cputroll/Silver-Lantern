# Independent containment re-review: v1.7.1

**Verdict: FIX-FIRST**  
Reviewed main `2218846` on Windows, 2026-11-18. This is a focused containment review, not a full release-gate sign-off.

## Remaining gap

The `verify_write_boundary` docstring (`Get-HeyListen.py:2021-2023`) still describes the substitution window as "the microseconds between this check and the open/replace". There is no such timing guarantee: scheduling or other delays can lengthen the interval. State simply that a same-user local actor can race the pathname-based check and subsequent open/replace; handle-relative operations would be needed for a stronger guarantee. This is a documentation accuracy issue, not a newly demonstrated remote escape.

Also, error classification is not unconditional: the stat-error handler constructs `InvalidGamePath(root.resolve(), ...)`, so if resolving the root itself raises `OSError`, that raw error escapes instead. The boundary still fails closed, but the promised classified error is not guaranteed in that case.

## Verified

- The boundary walk now calls `os.stat(..., follow_symlinks=False)` directly, skips `FileNotFoundError`, rejects other inspection errors, and preserves reparse-point, final-file type, and resolved-containment checks.
- `test_stat_failure_fails_closed` injects a permission error for an ancestor and verifies rejection. The prior Windows junction-swap behavioral tests remain present.
- Fresh execution of `python -m unittest test_write_boundary` exited 0:

  ```text
  Ran 9 tests in 0.813s

  OK
  ```

- The full suite and packaged friend simulation were not rerun for this focused review; release-gate sign-off remains separate.
