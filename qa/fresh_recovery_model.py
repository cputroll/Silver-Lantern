"""Aspirational offline protocol sketch; NOT a model or verification of the current publisher.

It assumes atomic revision reservation, immutable assets and recovery behavior not
established for GitHub or implemented by AutoShip. Never connects to a real service.
"""
import hashlib
import json
import re


class Stop(Exception):
    """Uncertain or invalid state: leave the channel untouched."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+(?:\.\d+)*', value):
        raise Stop('invalid version')
    parts = tuple(map(int, value.split('.')))
    return parts + (0,) * max(0, 4 - len(parts))


class FakeService:
    def __init__(self):
        self.assets = {}
        self.channel = {}
        self.tip = 0
        self.next_revision = 1
        self.interrupt_after = None
        self.push_mode = 'normal'
        self.legacy_writes = []

    def reserve(self):
        revision = self.next_revision
        self.next_revision += 1
        return revision

    def upload(self, name, data):
        if name in self.assets:
            raise Stop('immutable asset already exists')
        self.assets[name] = data
        if self.interrupt_after is not None:
            self.interrupt_after -= 1
            if self.interrupt_after == 0:
                raise Stop('interrupted upload')

    def push(self, expected_tip, channel):
        if self.push_mode == 'race':
            self.tip += 1
        if self.tip != expected_tip:
            raise Stop('tip changed')
        if self.push_mode == 'error':
            raise OSError('unknown outcome')
        self.channel = dict(channel)
        self.tip += 1
        if self.push_mode == 'commit_then_error':
            raise OSError('unknown outcome')


class Recovery:
    def __init__(self, service):
        self.s = service

    def verified_channel(self):
        c = self.s.channel
        if not c:
            if self.s.tip != 0:
                raise Stop('missing channel')
            return None
        try:
            revision = c['revision']
            if type(revision) is not int or revision < 1:
                raise ValueError()
            manifest = self.s.assets['manifest-' + str(revision)]
            if digest(manifest) != c['manifest_sha256']:
                raise ValueError()
            m = json.loads(manifest)
            package = self.s.assets['package-' + str(revision)]
            if (m['revision'] != revision or m['version'] != c['version']
                    or digest(package) != m['package_sha256']):
                raise ValueError()
            version(c['version'])
            return c
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise Stop('untrusted channel') from exc

    def publish(self, requested_version, package):
        requested = version(requested_version)
        tip = self.s.tip
        current = self.verified_channel()
        if current and requested < version(current['version']):
            raise Stop('version regression')
        revision = self.s.reserve()
        manifest = json.dumps({'revision': revision, 'version': requested_version,
                               'package_sha256': digest(package)}, sort_keys=True).encode()
        self.s.upload('package-' + str(revision), package)
        self.s.upload('manifest-' + str(revision), manifest)
        candidate = {'revision': revision, 'version': requested_version,
                     'manifest_sha256': digest(manifest)}
        # Verify the staged immutable objects before any channel update.
        if (self.s.assets.get('package-' + str(revision)) != package or
                self.s.assets.get('manifest-' + str(revision)) != manifest):
            raise Stop('staged asset mismatch')
        try:
            self.s.push(tip, candidate)
        except OSError as exc:
            # An error is success only if the exact candidate is now verified.
            if self.s.tip != tip + 1 or self.s.channel != candidate:
                raise Stop('ambiguous push') from exc
            self.verified_channel()
        return revision
