import hashlib
import unittest
from unittest.mock import patch

from channel_version_recovery import recover_version, fetch_range


def sha(data):
    return hashlib.sha256(data).hexdigest()


class RecoveryTests(unittest.TestCase):
    def fixture(self):
        old = b'version=1.2.3\n'
        return old, {'files': [{'path': 'hookshot_version.txt', 'size': len(old), 'sha256': sha(old), 'segments': [{'asset': 'r1-whole-bundle-001.tar', 'offset': 512, 'length': len(old), 'sha256': sha(old)}]}]}

    def test_interrupted_upload_uses_verified_old_bundle_not_new_local(self):
        old, manifest = self.fixture()
        calls = []
        def fetch(asset, offset, length):
            calls.append((asset, offset, length))
            return old
        self.assertEqual(recover_version(manifest, fetch), '1.2.3')
        self.assertEqual(calls, [('r1-whole-bundle-001.tar', 512, len(old))])

    def test_corrupt_or_short_segment_fails_closed(self):
        old, manifest = self.fixture()
        for content in (old[:-1], b'x' * len(old)):
            with self.assertRaises(ValueError):
                recover_version(manifest, lambda *_: content)

    def test_range_response_failures(self):
        class Response:
            def __init__(self, status, header, url='https://example.com/file'):
                self.status, self.headers, self.url = status, {'Content-Range': header}, url
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def geturl(self): return self.url
            def read(self, size): return b'abc'
        for response in (Response(200, 'bytes 5-7/100'), Response(206, 'bytes 0-2/100'), Response(206, 'bytes 5-7/100', 'http://example.com/file')):
            with patch('urllib.request.urlopen', return_value=response), self.assertRaises(ValueError):
                fetch_range('r1.tar', 5, 3)
        with patch('urllib.request.urlopen', return_value=Response(206, 'bytes 5-7/100')):
            self.assertEqual(fetch_range('r1.tar', 5, 3), b'abc')

    def test_rejects_unsafe_asset_and_ambiguous_version(self):
        old, manifest = self.fixture()
        manifest['files'][0]['segments'][0]['asset'] = '../bad'
        with self.assertRaises(ValueError):
            recover_version(manifest, lambda *_: old)
        old, manifest = self.fixture()
        manifest['files'].append(manifest['files'][0])
        with self.assertRaises(ValueError):
            recover_version(manifest, lambda *_: old)
