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
