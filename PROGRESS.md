# Installer concurrency investigation
- [x] Clone repository (D:; C: full).
- [x] Confirm existing Windows cross-process install-lock test passes.
- [x] Prove observed overlap / lock bypass root cause. (launcher.py started the install worker before opening the launcher log; a log-open failure orphaned the running worker. Fixed: log opens first, handler stops any started worker.)
- [x] Add safe single-worker behavior and bounded per-file retries. (launcher.py worker_command fix + regression tests; Get-HeyListen.py FILE_LOCK_CAP cumulative per-file retry cap escalating to the classified 'file in use' error.)
- [x] Add Restart Manager holder diagnostics and tests. (RstrtMgr.dll ctypes query on each lock-retry line: path + holder name[pid], holder=self, path-only on API failure.)
- [ ] Full suite, commit, push.

Evidence: launcher.py worker_command starts the child before opening LOG; if log open or processing raises, exception handler publishes error without terminating the worker. This is a concrete orphan path, not proof of observed overlap. Existing per-invocation retries are bounded; cumulative per-file behavior was unbounded across segments and re-entries - now capped by FILE_LOCK_CAP (900 s per file per run).
