"""Verify the actual production executable ignores inherited fixture overrides."""
import argparse
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--zip', required=True, type=Path)
args = parser.parse_args()
with tempfile.TemporaryDirectory(prefix='silver-trust-') as directory:
    root = Path(directory)
    with zipfile.ZipFile(args.zip) as archive:
        member = 'support/HeyListen-Get.exe'
        archive.extract(member, root)
    env = dict(os.environ, SILVER_LANTERN_GAME_MANIFEST=str(root / 'evil.json'),
               SILVER_LANTERN_TEST_ASSET_URL='http://127.0.0.1:1234/',
               SILVER_LANTERN_ALLOW_LOCAL_UPDATE='1')
    for name in ('SILVER_LANTERN_PAUSE_AT', 'SILVER_LANTERN_PAUSE_READY', 'SILVER_LANTERN_PAUSE_RELEASE'):
        env.pop(name, None)
    command = [str(root / member), '--cli', '--internal-trust-check']
    baseline = subprocess.run(command, env=env, capture_output=True, text=True, timeout=90, check=True)
    for checkpoint in ('assembly', 'sibling_race', 'pre_swap', 'post_swap'):
        env.update(SILVER_LANTERN_PAUSE_AT=checkpoint, SILVER_LANTERN_PAUSE_READY=str(root / 'ready'),
                   SILVER_LANTERN_PAUSE_RELEASE=str(root / 'release'))
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=90, check=True)
        if (result.returncode, result.stdout, result.stderr) != (baseline.returncode, baseline.stdout, baseline.stderr) or (root / 'ready').exists():
            raise SystemExit('Production behavior changed with QA pause environment')
    actual = json.loads(result.stdout.strip())
    expected = {'manifest_override': None,
                'asset_url': 'https://github.com/cputroll/Silver-Lantern/releases/download/game-v1/',
                'local_update_refused': True}
    if actual != expected:
        raise SystemExit(f'Production trust check failed: {actual!r}')
    print('PASS production trust check')
