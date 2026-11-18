# Independent review — v1.6.8 (2026-11-17, Terra reviewer)

Scope: v1.6.3..v1.6.8. Source review, not an end-to-end run. 59 focused tests OK.

## Major

1. **Interrupted self-update can remove the launcher's normal entry point.**
   `Get-HeyListen.py:1263-1276` renames the launcher to `.update-backup` before the replacement lands. Power loss between the two renames leaves the friend with no executable to open; recovery only runs from a subsequent update transaction, which cannot start.
   Fix: keep the original entry point launchable until the replacement is verified; add interruption tests at each swap step.

2. **Launcher relocation can delete an unrelated executable.**
   `launcher.py:603-633, 676` deletes every sibling matching `Ocarina of Time-*.exe` without proving we created it.
   Fix: track launcher copies in an ownership record; delete only recorded, verified copies.

3. **Interrupted downloads may not be adopted on the next run.**
   `launcher.py:544-558` discovers partial installs only via game-folder root markers, but installer partials live under `.heylisten-parts` (`Get-HeyListen.py:1867,1877`). A first download stopped early can be left unused while a second folder is chosen.
   Fix: recognize validated `.heylisten-parts` cache entries as adoptable; test restart before the first game file completes.

4. **Uninstall can race an installation or update.**
   Install holds `install-operation.lock` (`Get-HeyListen.py:2395-2401`); uninstall deletes files/registration without it (`launcher.py:702-754`).
   Fix: uninstall takes the same per-folder lock, with a clear busy message.

5. **Channel integrity failures are mislabeled as connectivity failures.**
   `launcher.py:1430-1443` shows "Could not reach the update server" for stale revisions, checksum mismatches, corrupt state and lock timeouts too.
   Fix: map each error class to its own plain-language problem/action.

## Minor

6. **GUI installation errors leak console detail and raw paths.**
   `launcher.py:924-936` appends the log path and exit code to friend-facing copy. Show problem/action only; keep "View log" as a separate button.

7. **Launcher-update failures omit problem and action.**
   `launcher.py:942-950` reduces updater failures to "Tool update failed (exit N)". Parse and show the updater's problem/action blocks; generic fallback otherwise.
