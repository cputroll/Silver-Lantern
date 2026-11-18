# Silver Lantern - agent notes

- Release pipeline: the CI workflow must build the package explicitly via release.ps1 -Tag <tag> -NoPublish before publishing; the test suite no longer produces dist-release/ as a side effect (isolated in d977f24).
