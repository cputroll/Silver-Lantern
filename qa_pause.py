"""QA-only deterministic checkpoint synchronization."""
import os
from pathlib import Path
import time


def pause(name, enabled):
    if not enabled or os.environ.get('SILVER_LANTERN_PAUSE_AT') != name:
        return
    release = Path(os.environ['SILVER_LANTERN_PAUSE_RELEASE'])
    ready = os.environ.get('SILVER_LANTERN_PAUSE_READY')
    if ready:
        Path(ready).write_text(str(os.getpid()), encoding='ascii')
    deadline = time.monotonic() + 30
    while not release.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f'QA pause timed out: {name}')
        time.sleep(.1)
