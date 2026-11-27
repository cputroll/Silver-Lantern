# HeyListen patches for Longshot v3.2.0

Game-channel revision 9 uses these four patches on [upstream Longshot](https://git.magicbean.dev/MagicBeanDev/Longshot), tag `v3.2.0`, commit `79d192c655b52ef69a42918e385ff79b6dfabd1f`.

From the root of a clean Longshot checkout, set `patchdir` to the absolute path of this patch directory and run the following in a POSIX shell. The numbered filenames expand in application order.

```sh
git switch --detach 79d192c655b52ef69a42918e385ff79b6dfabd1f
git am "${patchdir:?set patchdir first}"/000[1-4]-*.patch
```

- **0001:** Keep the Quit button at the top right of the menu and require confirmation before exiting.
- **0002:** Accept the legacy NGX SDK path and keep the release-specific version stamp out of source control.
- **0003:** Require a two-second Start+Back hold to open the menu.
- **0004:** Preserve Quit confirmation when the pointer leaves the button; add regression tests for pointer and focus behavior.

## Launcher packer: game-channel revision 10

Revision 9 shipped three files that Longshot rewrites during play. The next explicit install verification treated those changes as damage. Revision 10 is the first game-channel release packed without these files:

- `cache/longshot_prepared.bin`
- `cache/longshot_programs.bin`
- `cache/longshot_programs.idx`

[`launcher-port/`](launcher-port/) contains the complete fixed Longshot packer and its regression test, not another game patch. The root `pack-game.py` on this branch and on `main` lacks the port's earlier exclusions for Longshot launchers, settings, and backup files. The cache-only patch does not apply cleanly to that base. Use these files in the Longshot launcher port's `src` directory alongside its existing test helpers; run `python -m pytest test_mutable_game_caches.py` there.
