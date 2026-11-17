# Goal: safe executable auto-update
- [x] Establish goal-mode scheduling in this session (hourly task b1ca7e29-46b5-41da-9282-c649424d97e9).
- [x] Repair updater with safe staging, versioning, verification and rollback.
- [x] Test failure cases and real packaged EXE update. Packaged helper replacement/relaunch and missing-replacement rollback passed; mocked release-to-relaunch passed. Fresh suite: 15 tests OK. Live GitHub update remains untested until release.
- [ ] BLOCKED: Public release contains third-party game links; ask owner how to proceed before publishing. Manifest recovery, builder checksum and CI gates fixed. Update ZIP and checksum share a trust source (not signed). Live GitHub update untested until release. Goal and watchdog schedules deleted while blocked.
