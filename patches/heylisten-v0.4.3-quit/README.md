# HeyListen v0.4.3 in-game Quit fixes

This source patch contains the later tested Quit menu changes and their regression tests. It targets [Sacrappy/HeyListen v0.4.3, commit `08f78a9d5dcc5a0f4456d14664ed4f5d7b8c4476`](https://github.com/Sacrappy/HeyListen/commit/08f78a9d5dcc5a0f4456d14664ed4f5d7b8c4476).

Download `quit-fixes.patch` from this directory into the root of a clean HeyListen checkout at that commit. From the checkout root, run:

```sh
git apply --check quit-fixes.patch
git apply quit-fixes.patch
```

These later fixes have **not** been ported to Longshot or included in the game release.
