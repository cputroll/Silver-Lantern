# v1.6.9 gate results — NOT SIGNED OFF

Candidate source `6ba5042`, declared version 1.6.8. Production zip SHA-256 `5dd636bae968814fe459757230102c50155a9c7f8979a653f50a0ec2adebd605`; QA zip SHA-256 `1346ad9fc5654588ed32a605fa629196174a51ded0a8af388662a13a6713c084`. Build scripts reported packaging but their PowerShell wrappers returned exit 1. Production trust check passed. Full unittest discovery reported 399 tests OK, two skipped; its redirected PowerShell wrapper returned exit 1.

| Flow | Result |
| --- | --- |
| Early partial adoption | PASS on QA package. |
| Locked first temp write | PASS on QA package. |
| Concurrent uninstall | PASS: refused while install held the lock; unknown file preserved and install completed. |
| Sibling ownership race | FAIL: stale deletion and unknown preservation observed, but concurrent race not implemented; check intentionally fails closed. |
| Parent-only kill during assembly | Incomplete: no deterministic assembly synchronization; check intentionally fails closed. |
| Parent-only kill during lock retry | PASS: parent killed after retry message; child stopped and original entry point and unknown file survived. |
| Swap interruptions, all three boundaries | Incomplete: no deterministic phase synchronization; checks intentionally fail closed. |
| Previous-release update | PASS using rebuilt 0.0.1 QA fixture. |
| Production trust | PASS on candidate production artifact. |

Complete diagnostic gate exited 1. Existing install, GUI relocation, update, resume, final-file lock, uninstall, and safety checks passed. Do not tag or treat this as sign-off.

Gate log: `%LOCALAPPDATA%\Temp\opencode\silver-gate-6ba-final.log`; artifacts and individual logs: `%LOCALAPPDATA%\Temp\opencode\silver-gate-6ba-final`.

## Historical owner-accepted omissions (2026-11-17; resolved below)

The deterministic-synchronization variants of three scenarios were deferred by owner decision at the time of that run. All three are now **RESOLVED** by the packaged gate below.

## Current-source QA gate (2026-11-17)

Built from parent `ee6787b` plus these changes, source version 1.6.9, using `release.ps1 -Tag v1.6.9 -NoPublish` with and without `-Qa`. No tag was created. QA SHA-256 `91ea4bbc2162a3914b91d02240f80bc280b55b8eed824ddd6d975c35f0303f3b`; production SHA-256 `13eda38d9a87432edd8fbdd5967e3f7439e9c52e92e3733d1166bfa2117f4675`.

| Flow | Result |
| --- | --- |
| Sibling ownership race | RESOLVED / PASS: replacement rejected with Windows error 5; unknown replacement survives and recorded stale file is removed. |
| Parent-only kill during assembly | RESOLVED / PASS: child stops, no further assembly writes, rerun completes. |
| Pre-swap and post-swap interruption | RESOLVED / PASS: entry point remains present and rerun recovers. |
| Previous-release update | PASS using QA 0.0.1 fixture. |
| Remaining install, resume, locks, uninstall and safety flows | PASS. |
| Production trust | PASS on production archive; pause environment compared against unset for all four checkpoints. |
| Full unittest discovery | PASS: 441 tests, 2 skipped. |
| Handled retry diagnostics and plain crash detection | PASS: labeled retry tracebacks retained; plain tracebacks remain failures, using per-flow log windows. |

Complete gate exited 0. Per-flow logs: `D:/silver-hooks2-final2-gate`; combined log: `D:/silver-hooks2-temp/gate-final2.log`. The trust command does not itself execute the checkpoint paths; the production-disabled helper is covered separately by unit tests. Independent review remains required before any release tag.
