# Silver Lantern - agent notes

- Release pipeline: the CI workflow must build the package explicitly via release.ps1 -Tag <tag> -NoPublish before publishing; the test suite no longer produces dist-release/ as a side effect (isolated in d977f24).
- Before any release tag, the packaged zip must pass `qa/simulate-friend.ps1`, and an independent reviewer (a different agent/session from the author) must review the diff since the last tag and sign off in the PR, commit message, or `qa/reviews/`. See `qa/README.md`. Until the gate exists and passes, do not tag a release.
