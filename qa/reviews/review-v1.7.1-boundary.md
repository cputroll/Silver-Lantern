# Independent containment re-review: v1.7.1

**Verdict: FIX-FIRST**  
Reviewed main `700eb5c` on Windows, 2026-11-18. This is a focused containment review, not a full release-gate sign-off.

## Remaining gap

The stat-error handler at `Get-HeyListen.py:2042` still constructs `InvalidGamePath(root.resolve(), ...)`. If the ancestor `os.stat` fails and resolving the root also raises `OSError`, that raw error escapes instead of the classified `InvalidGamePath`. The check still fails closed, but the original error-classification gap remains. Pass `root` as given in this handler too.

## Verified

- The docstring at `Get-HeyListen.py:2021-2023` now correctly says the check narrows, but does not eliminate, the substitution window; it no longer asserts a microsecond bound.
- The separate resolution-failure handler at `Get-HeyListen.py:2051-2054` now raises `InvalidGamePath(root, ...)` without resolving again. This fixes that handler, but not the stat-error handler above.
- Fresh execution of `python -m unittest test_write_boundary` exited 0:

  ```text
  Ran 9 tests in 0.797s

  OK
  ```

- The full suite and packaged friend simulation were not rerun for this focused review; release-gate sign-off remains separate.
