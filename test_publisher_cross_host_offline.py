"""Offline cross-host integration of publisher primitives, not the held ship script.

All Git remotes and release assets live under a temporary directory.  These tests
exercise recovery and contention without contacting GitHub or bypassing a hold.
"""
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import channel_publisher as publisher


class OfflinePublisherRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / 'remote.git'
        self.assets = self.root / 'release-assets'
        self.assets.mkdir()
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_TERMINAL_PROMPT='0')
        self.git('init', '--bare', str(self.remote))
        self.a = self.clone('host-a', initial=True)
        initial = self.make_asset('game-manifest-1.json', b'old manifest')
        self.commit_pointer(self.a, publisher.make_pointer(None, 1, initial, self.digest(initial)))
        self.git('push', 'origin', 'HEAD:refs/heads/game-channel', cwd=self.a)
        self.b = self.clone('host-b')

    def git(self, *args, cwd=None, check=True):
        return subprocess.run(['git', *args], cwd=cwd, env=self.env, text=True,
                              capture_output=True, check=check, timeout=15)

    def clone(self, name, initial=False):
        path = self.root / name
        args = ['clone'] + ([] if initial else ['--branch', 'game-channel'])
        self.git(*args, str(self.remote), str(path))
        self.git('config', 'user.name', 'Offline Test', cwd=path)
        self.git('config', 'user.email', 'test@example.invalid', cwd=path)
        return path

    def make_asset(self, name, data):
        (self.assets / name).write_bytes(data)
        return name

    def digest(self, name):
        return hashlib.sha256((self.assets / name).read_bytes()).hexdigest()

    def pointer(self, repo):
        return json.loads((repo / 'channel.json').read_text(encoding='utf-8'))

    def remote_pointer(self):
        text = self.git('--git-dir', str(self.remote), 'show', 'refs/heads/game-channel:channel.json').stdout
        return json.loads(text)

    def commit_pointer(self, repo, pointer):
        (repo / 'channel.json').write_text(json.dumps(pointer), encoding='utf-8')
        self.git('add', 'channel.json', cwd=repo)
        self.git('commit', '-m', 'channel revision ' + str(pointer['revision']), cwd=repo)

    def test_interrupted_upload_leaves_channel_unchanged_and_fresh_host_can_recover(self):
        source = self.root / 'packed'
        source.mkdir()
        (source / 'r2-bundle.bin').write_bytes(b'partial candidate')
        (source / 'game-manifest-2.json').write_bytes(b'candidate manifest')
        names = ['r2-bundle.bin', 'game-manifest-2.json']

        def upload(name):
            if name == names[1]:
                raise RuntimeError('simulated host loss')
            (self.assets / name).write_bytes((source / name).read_bytes())

        def unexpected(_=None):
            self.fail('verification or channel advance after interrupted upload')

        with self.assertRaisesRegex(RuntimeError, 'host loss'):
            publisher.publish_verified(names, upload, unexpected, unexpected)
        self.assertEqual(self.remote_pointer()['revision'], 1)
        self.assertTrue((self.assets / names[0]).exists())
        self.assertFalse((self.assets / names[1]).exists())

        # Recovery uses fresh immutable names, never overwrites the orphan.
        recovered = {'r3-bundle.bin': b'recovered bundle', 'game-manifest-3.json': b'recovered manifest'}
        def upload_fresh(name):
            with (self.assets / name).open('xb') as target:
                target.write(recovered[name])
        def verify(name):
            self.assertEqual((self.assets / name).read_bytes(), recovered[name])
        def advance():
            pointer = publisher.make_pointer(self.pointer(self.b), 3, 'game-manifest-3.json',
                                             self.digest('game-manifest-3.json'))
            self.commit_pointer(self.b, pointer)
            publisher.advance_ref(self.b, 'origin')
        publisher.publish_verified(list(recovered), upload_fresh, verify, advance,
                                   existing=[p.name for p in self.assets.iterdir()])
        self.assertEqual(self.remote_pointer()['revision'], 3)
        self.assertEqual(self.remote_pointer()['previous']['revision'], 1)
        self.assertEqual((self.assets / names[0]).read_bytes(), b'partial candidate')

    def test_competing_hosts_keep_winning_pointer_and_loser_assets(self):
        old_a, old_b = self.pointer(self.a), self.pointer(self.b)
        for repo, old, revision in ((self.a, old_a, 2), (self.b, old_b, 3)):
            name = self.make_asset(f'game-manifest-{revision}.json', f'host {revision}'.encode())
            self.commit_pointer(repo, publisher.make_pointer(old, revision, name, self.digest(name)))
        publisher.advance_ref(self.a, 'origin')
        with self.assertRaises(subprocess.CalledProcessError):
            publisher.advance_ref(self.b, 'origin')
        pointer = self.remote_pointer()
        self.assertEqual(pointer['revision'], 2)
        self.assertEqual(pointer['sha256'], self.digest(pointer['manifest']))
        self.assertEqual((self.assets / 'game-manifest-3.json').read_bytes(), b'host 3')
        # A new host sees the winner before choosing its next revision.
        fresh = self.clone('host-c')
        self.assertEqual(self.pointer(fresh), pointer)


if __name__ == '__main__':
    unittest.main()
