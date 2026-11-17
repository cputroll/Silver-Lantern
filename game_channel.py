"""Resolve the atomic game channel to a verified immutable release asset."""
import hashlib
import json
import re
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

CHANNEL_URL = 'https://raw.githubusercontent.com/cputroll/Silver-Lantern/game-channel/channel.json'
ASSET_BASE = 'https://github.com/cputroll/Silver-Lantern/releases/download/game-v1/'
LEGACY_URL = ASSET_BASE + 'game-manifest.json'


def load_state(folder):
    path = Path(folder) / '.heylisten-get' / 'channel-state.json'
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return 0, None
    revision, digest = state['revision'], state['sha256']
    if type(revision) is not int or revision < 1 or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('invalid saved game channel state')
    return revision, digest


@contextmanager
def state_lock(directory, timeout=10, name='channel-state.lock'):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / name).open('a+b') as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                lock.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('game channel state lock timed out')
                time.sleep(.05)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def install_lock(folder, timeout=10):
    return state_lock(Path(folder) / '.heylisten-get', timeout, name='install-operation.lock')


def save_state(folder, revision, digest):
    if type(revision) is not int or revision < 1 or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('invalid game channel state')
    directory = Path(folder) / '.heylisten-get'
    with state_lock(directory):
        old_revision, old_digest = load_state(folder)
        if revision < old_revision or (revision == old_revision and digest != old_digest):
            raise ValueError('game channel state would regress')
        fd, temporary = tempfile.mkstemp(dir=directory, prefix='channel-', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump({'revision': revision, 'sha256': digest}, out)
            os.replace(temporary, directory / 'channel-state.json')
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class StaleChannel(ValueError):
    """The channel is older than a previously verified revision."""


def resolve_info(fetch, minimum_revision=0, expected_digest=None, pointer_data=None):
    pointer = json.loads(fetch(CHANNEL_URL) if pointer_data is None else pointer_data)
    revision = pointer.get('revision', 0)
    if type(revision) is not int or revision < 1:
        raise ValueError('invalid game channel revision')
    if revision < minimum_revision:
        raise StaleChannel('stale game channel')
    name = pointer.get('manifest')
    digest = pointer.get('sha256')
    if (pointer.get('version') != 1 or not isinstance(name, str)
            or not re.fullmatch(r'game-manifest-[A-Za-z0-9._-]+\.json', name)
            or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
        raise ValueError('invalid game channel')
    if revision == minimum_revision and expected_digest is not None and digest != expected_digest:
        raise ValueError('game channel revision changed content')
    data = fetch(ASSET_BASE + name)
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('game channel manifest checksum mismatch')
    return data, revision, digest


def resolve(fetch, minimum_revision=0, expected_digest=None, pointer_data=None):
    return resolve_info(fetch, minimum_revision, expected_digest, pointer_data)[0]


def resolve_with_fallback(fetch, minimum_revision=0, expected_digest=None):
    try:
        pointer = fetch(CHANNEL_URL)
    except FileNotFoundError:
        if minimum_revision:
            raise
        return fetch(LEGACY_URL)
    return resolve(fetch, minimum_revision, expected_digest, pointer)
