# Silver Lantern 1.5.0

- **Faster installation:** A streaming pipeline overlaps downloads and file assembly, with startup improvements. In testing, installs were roughly three times faster than the earlier release; actual speeds vary.
- **More resilient downloads:** Checkpoint recovery retries a damaged bundle rather than restarting the entire installation, and verifies incomplete files before resuming.
- **Saves kept by default:** Uninstall now preserves saves. Use `--purge-saves` if you explicitly want to remove them too.
