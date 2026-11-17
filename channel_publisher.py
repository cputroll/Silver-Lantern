"""Validation primitives for publishing immutable game-channel pointers."""
import re
import hashlib
from pathlib import Path
import subprocess


def publish_verified(assets, upload, verify, advance):
    for asset in assets:
        upload(asset)
    for asset in assets:
        verify(asset)
    advance()


def validate_assets(root, assets):
    seen = set()
    for asset in assets:
        name = asset['asset']
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*', name) or name.casefold() in seen:
            raise ValueError('invalid or duplicate asset name')
        seen.add(name.casefold())
        path = Path(root) / name
        h = hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda: f.read(8 * 1024 * 1024), b''):
                h.update(chunk)
        if path.stat().st_size != asset['size'] or h.hexdigest() != asset['sha256']:
            raise ValueError('asset checksum mismatch')


def advance_ref(worktree, remote):
    subprocess.run(['git', 'push', remote, 'HEAD:refs/heads/game-channel'], cwd=worktree, check=True, timeout=60, capture_output=True)


def make_pointer(old, revision, asset, digest):
    previous = 0 if old is None else old.get('revision')
    if type(previous) is not int or previous < 0 or type(revision) is not int or revision <= previous:
        raise ValueError('revision must increase')
    if not isinstance(asset, str) or not re.fullmatch(r'game-manifest-[A-Za-z0-9._-]+\.json', asset):
        raise ValueError('invalid manifest asset')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('invalid manifest digest')
    return {'version': 1, 'revision': revision, 'manifest': asset, 'sha256': digest}
