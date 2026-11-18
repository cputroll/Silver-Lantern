"""Offline publication recovery model. Never connects to a real release or channel.

This is an aspirational test model, not a production publisher or proof of its safety.
It uses different schemas and idealized atomic/consistent remote primitives; the real
GitHub/Git protocol and crash recovery must be independently implemented and tested.
"""
import hashlib
import json
import re
import threading


class Conflict(Exception):
    pass


class Ambiguous(Exception):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def version_key(value):
    if not re.fullmatch(r'\d+(?:\.\d+)*', value):
        raise ValueError('version must contain numeric components only')
    parts = [int(part) for part in value.split('.')]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


class Store:
    def __init__(self):
        self.assets = {}
        self.channel = None
        self.generation = 0
        self.ambiguous_before_commit = False
        self.ambiguous_after_commit = False
        self.lock = threading.RLock()

    def put(self, name, data):
        with self.lock:
            if name in self.assets and self.assets[name] != data:
                raise Conflict('immutable asset collision')
            self.assets[name] = data

    def snapshot(self):
        with self.lock:
            return self.generation, None if self.channel is None else self.channel.copy()

    def verify_channel(self, pointer=None):
        with self.lock:
            pointer = self.channel if pointer is None else pointer
            if pointer is None:
                return None
            try:
                raw = self.assets[pointer['manifest']]
                if digest(raw) != pointer['sha256']:
                    raise Conflict('manifest digest mismatch')
                manifest = json.loads(raw)
                if manifest['version'] != pointer['version']:
                    raise Conflict('version mismatch')
                for asset in manifest['assets']:
                    data = self.assets[asset['name']]
                    if len(data) != asset['size'] or digest(data) != asset['sha256']:
                        raise Conflict('bundle mismatch')
                return manifest['version']
            except (KeyError, ValueError, TypeError) as exc:
                raise Conflict('invalid or incomplete channel') from exc

    def cas(self, expected, pointer):
        with self.lock:
            if self.ambiguous_before_commit:
                self.ambiguous_before_commit = False
                raise Ambiguous()
            if self.generation != expected:
                raise Conflict('stale channel generation')
            self.verify_channel(pointer)
            self.channel = pointer.copy()
            self.generation += 1
            if self.ambiguous_after_commit:
                self.ambiguous_after_commit = False
                raise Ambiguous()


class Publisher:
    def __init__(self, store, version, bundles):
        self.store = store
        self.version = version
        version_key(version)
        if not bundles:
            raise ValueError('at least one bundle is required')
        self.bundles = dict(bundles)
        self.bundle_names = []
        self.pointer = None

    def prepare(self):
        entries = []
        self.bundle_names = []
        for name, data in sorted(self.bundles.items()):
            if not isinstance(data, bytes) or not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
                raise ValueError('invalid bundle')
            sha = digest(data)
            asset_name = f'bundle-{sha}-{name}'
            self.store.put(asset_name, data)
            self.bundle_names.append(asset_name)
            entries.append({'name': asset_name, 'size': len(data), 'sha256': sha})
        raw = json.dumps({'version': self.version, 'assets': entries}, sort_keys=True, separators=(',', ':')).encode()
        sha = digest(raw)
        manifest = f'manifest-{sha}.json'
        self.store.put(manifest, raw)
        self.pointer = {'version': self.version, 'manifest': manifest, 'sha256': sha}
        self.store.verify_channel(self.pointer)

    def recover(self):
        self.prepare()
        for _ in range(8):
            generation, current = self.store.snapshot()
            if current is not None:
                self.store.verify_channel(current)
                if current == self.pointer:
                    return
                if version_key(current['version']) >= version_key(self.version):
                    raise Conflict('channel already has equal or newer version')
            try:
                self.store.cas(generation, self.pointer)
            except (Conflict, Ambiguous):
                # A lost response or racing writer requires a fresh verified read.
                continue
            return
        raise Conflict('reconciliation did not converge')
