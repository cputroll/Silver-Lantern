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

Built from parent `f281c04` plus these changes, source version 1.6.9, using `release.ps1 -Tag v1.6.9 -NoPublish` with and without `-Qa`. No tag was created. QA SHA-256 `f8ec07babab5c313e75bfa26f75d7f6241fb7082ffa94f9cf751abb83679f9b3`; production SHA-256 `ccfae77862ffac8ef82af85cb3b2101accc26d4d2a8ed9a1e1edd8342a98fc29`.

| Flow | Result |
| --- | --- |
| Sibling ownership race | RESOLVED / PASS: replacement rejected with Windows error 5; unknown replacement survives and recorded stale file is removed. |
| Parent-only kill during assembly | RESOLVED / PASS: child stops, no further assembly writes, rerun completes. |
| Pre-swap and post-swap interruption | RESOLVED / PASS: entry point remains present and rerun recovers. |
| Previous-release update | PASS using QA 0.0.1 fixture. |
| Remaining install, resume, locks, uninstall and safety flows | PASS. |
| Production trust | PASS on production archive; pause environment compared against unset for all four checkpoints. |
| Full unittest discovery | PASS: 433 tests, 2 skipped. |

Complete gate exited 0. Per-flow logs: `D:/silver-hooks2-tip-gate`; combined log: `D:/silver-hooks2-temp/gate-tip.log`. The trust command does not itself execute the checkpoint paths; the production-disabled helper is covered separately by unit tests. Independent review remains required before any release tag.
