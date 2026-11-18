# Independent review — v1.6.9 sign-off

**Verdict: FIX-FIRST**  
Reviewed main at `e282559c86cec640ec4dce0521fc10cf072584d8` against `v1.6.8` and the pre-v1.6.9 findings. This is not authorization to tag; the packaged shadow gate remains required.

## Findings

### Medium — packaged gate still omits previously required adversarial scenarios

`qa/simulate_friend.py:174-215,256-270,348-357`. The listed packaged checks interrupt after a complete bundle, lock the final destination, and run uninstall separately. They do not assert early partial adoption before any completed game file, a locked first write to `.heylisten-new`, uninstall concurrent with install, a sibling-ownership race, parent-only termination during assembly/retry, or interruption at swap boundaries. The identity-aware descendant monitor improves zombie detection but does not supply those adversarial scenarios. Unit tests do not establish their behavior in a packaged release, and the documented 33-second prior-update timeout does not test interruption safety.

**Fix:** add independently asserted packaged scenarios covering these cases, including preservation of unknown files and original entry point at each swap boundary; run and record the complete QA shadow gate alongside the production trust check before tagging. Clearly document any explicitly owner-accepted omissions.

### Medium — parent identity can be reused before the first handle acquisition

`launcher.py:1031-1034`; `Get-HeyListen.py:526-560`. The worker receives only the parent's numeric PID and opens its single process handle lazily at the first `check_parent_alive()`. If the launcher exits and its PID is reused before that first open, the handle identifies an unrelated process and subsequent checks can permit the orphaned worker to continue. Retaining the handle correctly prevents reuse *after* acquisition, but does not close this startup race.

**Fix:** establish original identity at creation, preferably pass an explicitly inherited process handle; alternatively pass and verify creation time with the opened handle, failing closed on mismatch. Add a deterministic test simulating PID reuse before the first check and a packaged parent-only kill test.

## Verification and residual risk

- Production source guards the manifest, asset URL and unsigned local update seams with generated `QA_SEAMS`; frozen builds fail closed if the module is absent. `release.ps1` separately names QA output and refuses publishing it; `qa/check_production_trust.py` checks the executable extracted from the production zip. No package was available in this review clone, so the actual candidate artifact and QA/production distinction were **not independently executed**. Source-level guards are not a substitute for the packaged check.
- Ownership cleanup hashes and marks deletion through the same deny-write/deny-delete Windows handle; relocation record updates are under a destination lock and no longer invoke sibling cleanup during relocation. This addresses the previously identified pathname deletion race and lost concurrent relocation records, subject to the packaged race coverage above.
- Parent checks now cover copy/hash/assembly loops and replacement retries, with a retained Windows handle after acquisition. Blocking system I/O is not instantaneously cancellable, and a check immediately before a replacement cannot make parent exit and replacement atomic.
- Descendant tracking uses PID plus creation time. The gate documents that sampling can miss short-lived reparented processes. The update phase instrumentation and ten loaded trials are recorded in `qa/update-stress-results.json`; the earlier approximately 90-second outlier remains unexplained and the 33-second deadline is a gate bound, not proof that the outlier cannot recur.
- Focused source tests (`test_build_flags`, `test_production_seams`, `test_concurrency_ownership`, `test_parent_liveness`, `test_simulate_friend`, `test_install_file_lock`, `test_updater`) reported **46 tests OK**. The PowerShell output wrapper returned exit code 1 despite the unittest OK summary; no packaged build or shadow gate was run here.
