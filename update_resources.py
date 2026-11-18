"""Locate trusted game-update resources in source and frozen builds."""
import sys
from pathlib import Path


def resolve_update_script(base: Path):
    if getattr(sys, 'frozen', False):
        bundle = getattr(sys, '_MEIPASS', None)
        if not bundle:
            return None
        candidates = (Path(bundle) / 'Update-Game.ps1',)
    else:
        base = Path(base)
        candidates = (base / 'Update-Game.ps1', base / 'support' / 'Update-Game.ps1')
    return next((path for path in candidates if path.is_file()), None)
