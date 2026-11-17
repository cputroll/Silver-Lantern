"""Validation primitives for publishing immutable game-channel pointers."""
import re


def make_pointer(old, revision, asset, digest):
    previous = 0 if old is None else old.get('revision')
    if type(previous) is not int or previous < 0 or type(revision) is not int or revision <= previous:
        raise ValueError('revision must increase')
    if not isinstance(asset, str) or not re.fullmatch(r'game-manifest-[A-Za-z0-9._-]+\.json', asset):
        raise ValueError('invalid manifest asset')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('invalid manifest digest')
    return {'version': 1, 'revision': revision, 'manifest': asset, 'sha256': digest}
