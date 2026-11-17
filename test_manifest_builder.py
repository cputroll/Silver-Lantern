import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_updater import app


class ManifestBuilderTests(unittest.TestCase):
    def test_malformed_local_falls_back_to_cache(self):
        with tempfile.TemporaryDirectory() as td:
            local, cache = Path(td) / 'sources.json', Path(td) / 'cache.json'
            local.write_text('{bad')
            cache.write_text(json.dumps({'game': {}, 'builder': {}}))
            with mock.patch.object(app, 'MANIFEST_LOCAL', local), mock.patch.object(app, 'MANIFEST_CACHE', cache):
                self.assertEqual(app.load_manifest(None), {'game': {}, 'builder': {}})

    def test_cache_write_failure_does_not_block_valid_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            local = Path(td) / 'sources.json'
            local.write_text(json.dumps({'game': {}, 'builder': {}}))
            with mock.patch.object(app, 'MANIFEST_LOCAL', local), mock.patch.object(app, 'MANIFEST_CACHE', Path(td)), mock.patch.object(Path, 'write_text', side_effect=PermissionError('read only')):
                self.assertEqual(app.load_manifest(None), {'game': {}, 'builder': {}})

    def test_version_never_loads_manifest(self):
        with mock.patch.object(sys, 'argv', ['app', '--version']), mock.patch.object(app, 'load_manifest', side_effect=AssertionError('manifest loaded')), mock.patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(app.main(), 0)

    def test_builder_rejects_missing_invalid_or_ambiguous_checksums(self):
        for sums in (None, '', 'a' * 64 + '  other-builder.exe', 'invalid  builder.exe', ('a' * 64 + '  builder.exe\n') * 2):
            with self.subTest(sums=sums), tempfile.TemporaryDirectory() as td:
                assets = [{'name': 'builder.exe', 'size': 3, 'browser_download_url': 'builder'}]
                if sums is not None:
                    assets.append({'name': 'SHA256SUMS.txt', 'browser_download_url': 'sums'})
                response = mock.Mock(text=sums)
                session = mock.Mock()
                session.get.return_value = response
                with mock.patch.object(app, 'latest_release', return_value={'tag_name': 'v1', 'assets': assets}), mock.patch.object(app, 'stream_to') as download:
                    with self.assertRaises((ValueError, RuntimeError)):
                        app.download_builder(session, {'builder': {'repo': 'x/y', 'asset': 'builder.exe'}}, Path(td))
                    download.assert_not_called()


if __name__ == '__main__':
    unittest.main()
