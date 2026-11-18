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
    result = subprocess.run([str(root / member), '--cli', '--internal-trust-check'],
                            env=env, capture_output=True, text=True, timeout=90, check=True)
    actual = json.loads(result.stdout.strip())
    expected = {'manifest_override': None,
                'asset_url': 'https://github.com/cputroll/Silver-Lantern/releases/download/game-v1/',
                'local_update_refused': True}
    if actual != expected:
        raise SystemExit(f'Production trust check failed: {actual!r}')
    print('PASS production trust check')
