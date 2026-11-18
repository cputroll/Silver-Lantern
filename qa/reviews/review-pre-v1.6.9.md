# Independent review — pre-v1.6.9

**Verdict: FIX-FIRST**  
Scope: `v1.6.8..main`, reviewed against `qa/reviews/review-v1.6.8.md`. Source review and 84 focused unit tests; packaged friend simulation not run in this review. The tests reported OK. This is not a sign-off on the release gate.

## Findings

### High — fixture manifest can replace authenticated channel in the shipped GUI

`launcher.py:36,499-510,1720-1721`; `Get-HeyListen.py:1542-1550,2039-2067`. The so-called test-only manifest override is active in normal packaged builds, without the dummy GUI guard. Set `SILVER_LANTERN_GAME_MANIFEST` in the inherited environment and the GUI accepts an attacker-chosen local manifest instead of the pinned channel; on first install it passes that manifest to the installer. Coupled with `SILVER_LANTERN_TEST_ASSET_URL`, an attacker-controlled loopback service can supply matching arbitrary game executable bytes and hashes. The attacker needs influence over the friend's process environment and a local service/manifest; ordinary remote channel or config control alone was not established as sufficient. The resulting hashes authenticate only attacker-supplied bytes, not the release. Existing CLI `--game-manifest` is an explicit operator choice, but this inherited ambient GUI override is not.

**Fix:** exclude fixture overrides from production artifacts, or enforce a build-time test-only flag unavailable in release binaries; do not rely on another environment variable as the security boundary. Add packaged-release tests proving these variables cannot redirect manifest or assets. The unsigned `SILVER_LANTERN_ALLOW_LOCAL_UPDATE` branch (`Get-HeyListen.py:1463-1476`) also ships: it additionally requires explicit `--self-update-from`, so environment alone does not trigger it, but it should likewise be compiled out of release artifacts rather than relying on ambient opt-in.

### Medium — ownership verification and deletion are not atomic or serialized

`launcher.py:679-702,740-747`. A recorded sibling is hashed by pathname and then separately unlinked by pathname. A concurrent actor can replace it between hash and unlink, so an unowned replacement can be deleted. Concurrent relocations also perform unsynchronized read-modify-write of the same JSON record: records can be lost, and one process can delete another process's just-recorded target before that process launches it. This is a local concurrency/TOCTOU issue, not a claim of remote exploitation. Matching hashes substantially improve the original unconditional wildcard deletion, but do not fully establish the claimed safe ownership guarantee under races.

**Fix:** serialize relocation, ownership-record updates and cleanup using a stable per-destination lock; use Windows handle-based identity verification/deletion or otherwise ensure the verified file identity is the one deleted. Test concurrent relocations and replacement between verification and deletion. Treat the record as untrusted metadata; never use it alone as proof of ownership.

### Medium — parent-liveness checks can miss exit during assembly and final replacement

`Get-HeyListen.py:546-566,2095-2116` (also segmented assembly and replacement paths). Parent checks happen at selected boundaries, not during every long copy/hash/write or `_replace_with_retry`; if the GUI exits just after the check, a child can continue assembling and replace a game file, including after a lock retry up to 60 seconds. Windows checks also reopen by PID each time (`513-539`), so PID reuse can falsely identify an unrelated process as the original parent. The existing test stops a child in its exercised loop, not these windows.

**Fix:** retain a handle to the original parent process for the lifetime of the child, check cancellation within long loops and immediately before each commit/replacement and on every retry, and define bounded shutdown behavior for blocked I/O. Add tests killing the actual GUI parent during a delayed assembly and while the final target is locked, verifying no later replacement or surviving child.

### Medium — packaged simulation omits required adversarial cases

`qa/simulate_friend.py:85-98,123-167,202-220,226-249,297-305`. Update only checks a stale backup, not interruption around every swap boundary; resume waits until the first file has completed, so it does not test adoption of a partial before any game file; lock test locks the destination, not the first write to a locked `.heylisten-new`; uninstall is not run concurrently with installation; there is no sibling-ownership race or parent-only kill case. The harness therefore cannot by itself detect regressions in several fixes it is meant to gate. It also exercises test-enabled behavior rather than asserting production fixture overrides are absent.

**Fix:** add independently asserted packaged scenarios for each boundary, including first-write lock, concurrent uninstall, early partial adoption, kill-parent-only, interrupted swap, and ownership/unknown-file preservation. Run them against the exact release artifact, and separately verify production trust settings.

## Prior finding verification

- **Entry point swap:** staging, flush, hash verification and same-directory replace retain the old target until atomic replacement (`Get-HeyListen.py:1314-1345`). A pre-existing orphan backup from an older release cannot repair a missing entry point without another executable starting the transaction. No power-loss or filesystem durability test was run.
- **Sibling deletion:** wildcard deletion replaced with per-name SHA-256 records, but the race above remains.
- **Partial adoption:** nonempty matching cache partials are detected (`launcher.py:570-604`); this is filename/size validation, not proof that a partial belongs to the current manifest. The installer must validate data before use.
- **Install/uninstall locking:** both take `install-operation.lock`; install takes channel-state lock inside it, and uninstall does not take channel-state lock, so no reverse lock ordering was found in the reviewed paths. The lock serializes the covered file operations, not every separate GUI action outside that transaction.
- **Error honesty:** typed channel errors have distinct messages and child problem/action blocks are parsed; GUI generic fallback remains for unclassified errors.
- **First-write retry:** bounded monotonic deadline and backoff avoid an unbounded retry loop; cancellation gap above remains. Permission errors beyond sharing violations are retried until the deadline.
- **Parent shutdown:** cooperative checks improve behavior but do not guarantee immediate stop or stable parent identity as described above.

Verification: `python -m unittest test_updater test_install_file_lock test_parent_liveness test_copyfix_adoption test_copyfix_channel test_launcher test_simulate_friend` reported **84 tests OK**. The shell wrapper returned a nonzero exit despite that unittest summary, so the wrapper's status was not used as proof of success. No production package, release tag, or deployment was changed.
