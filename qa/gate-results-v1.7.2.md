# v1.7.2 release gate

Candidate built locally with `release.ps1 -Tag v1.7.2 -NoPublish` and `-NoPublish -Qa`; both returned 0. Production ZIP SHA-256: `11a86e37b3322634ebd6dac741bebf5195d0fdd65ac9cdfce59c63b56e1b2d1e`; its checksum file matched. `qa/check_production_trust.py` passed with hostile trust-seam environment variables set. Full pytest after the checkpoint fix: 482 passed, 4 skipped, 110 subtests passed. Independent final review: `qa/reviews/review-v1.7.2.md`.

The earlier friend simulation failed first-temp-write-lock safety due an unlabelled chained checkpoint traceback; the checkpoint fix was subsequently reviewed and a fresh full simulation is required. The packaged relocated updater test checks offline validation and bundled preset structure, not a successful real update. The MSI real game update stopped at missing NVIDIA SDK; it is not claimed as passed.
