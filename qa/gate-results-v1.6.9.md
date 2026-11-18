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

## Owner-accepted omissions (2026-11-17)

The deterministic-synchronization variants of three scenarios are deferred by owner decision, with their protections already verified by unit tests and a direct packaged attack that Windows rejected (error 5): sibling-ownership concurrent swap (rejected-swap proof recorded), parent-only kill during assembly, and swap-boundary interruption timing. The implemented packaged flows above all PASS; production trust check PASSES.
