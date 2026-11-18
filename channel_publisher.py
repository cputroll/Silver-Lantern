"""Validation primitives for publishing immutable game-channel pointers."""
import re
import hashlib
from pathlib import Path
import subprocess


def publish_verified(assets, upload, verify, advance, existing=()):
    names = {str(item).casefold() for item in existing}
    if any(str(asset).casefold() in names for asset in assets):
        raise FileExistsError('immutable asset already exists')
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


def push_with_reconciliation(worktree, remote, expected, candidate):
    """Push once; after an uncertain result only the exact remote commit proves success.

    A conflicting, absent, or unreadable ref remains unresolved; never retry a stale
    lease or claim success based on a revision number or a local checkout.
    """
    if expected and not re.fullmatch(r'[0-9a-f]{40,64}', expected):
        raise ValueError('invalid expected commit')
    if not re.fullmatch(r'[0-9a-f]{40,64}', candidate):
        raise ValueError('invalid candidate commit')
    ref = 'refs/heads/game-channel'
    try:
        resolved = subprocess.run(['git', 'remote', 'get-url', '--push', '--all', remote],
                                  cwd=worktree, check=True, timeout=60, capture_output=True, text=True)
        destinations = resolved.stdout.splitlines()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
        raise RuntimeError('cannot resolve push destination') from error
    if len(destinations) != 1 or not destinations[0].strip():
        raise RuntimeError('exactly one push destination required')
    destination = destinations[0]
    try:
        subprocess.run(['git', 'push', f'--force-with-lease={ref}:{expected}', destination,
                        f'{candidate}:{ref}'], cwd=worktree, check=True, timeout=60, capture_output=True)
        return 'published'
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
        try:
            result = subprocess.run(['git', 'ls-remote', '--refs', destination, ref], cwd=worktree,
                                    check=True, timeout=60, capture_output=True, text=True)
            lines = result.stdout.splitlines()
            if len(lines) == 1 and lines[0].split() == [candidate, ref]:
                return 'published'
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            pass
        raise RuntimeError('channel push unresolved or superseded; reconciliation required') from error


def make_pointer(old, revision, asset, digest):
    previous = 0 if old is None else old.get('revision')
    if type(previous) is not int or previous < 0 or type(revision) is not int or revision <= previous:
        raise ValueError('revision must increase')
    if not isinstance(asset, str) or not re.fullmatch(r'game-manifest-[A-Za-z0-9._-]+\.json', asset):
        raise ValueError('invalid manifest asset')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('invalid manifest digest')
    pointer = {'version': 1, 'revision': revision, 'manifest': asset, 'sha256': digest}
    if old is not None:
        pointer['previous'] = {key: old[key] for key in ('version', 'revision', 'manifest', 'sha256')}
    return pointer


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 5:
        raise SystemExit('usage: channel_publisher.py WORKTREE REMOTE EXPECTED CANDIDATE')
    worktree, remote, expected, candidate = sys.argv[1:]
    push_with_reconciliation(worktree, remote, '' if expected == '-' else expected, candidate)
