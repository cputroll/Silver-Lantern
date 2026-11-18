"""Offline tests for the unintegrated HTTPS transport."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from git_isolated_https import IsolatedHttpsGit, InvalidDestination


class HttpsIsolationTests(unittest.TestCase):
    def test_rejects_unsafe_urls_without_running_git(self):
        for url in ('http://example.test/r', 'https://u:p@example.test/r',
                    'https://example.test/r?token=x', 'https://example.test/r#x',
                    'https://example.test/r\nextra', 'https://example.test/r%0a'):
            with self.subTest(url=url), self.assertRaises(InvalidDestination):
                IsolatedHttpsGit('unused', url, allowed_root='unused', username='u', password='secret')

    def test_rejects_dot_segments_before_source_validation(self):
        paths = ('/./repo', '/../repo', '/org/./repo', '/org/../repo',
                 '/org/.', '/org/..', '/org/%2e%2e/repo')
        with patch('git_isolated_https.IsolatedGit.__init__', return_value=None) as parent:
            for path in paths:
                with self.subTest(path=path), self.assertRaises(InvalidDestination):
                    IsolatedHttpsGit('unused', 'https://example.test' + path,
                                     allowed_root='unused', username='u', password='secret')
            parent.assert_not_called()

    def test_allows_dots_within_path_segments(self):
        with patch('git_isolated_https.IsolatedGit.__init__', return_value=None):
            context = IsolatedHttpsGit('unused', 'https://example.test/my.org/repo.git',
                                       allowed_root='unused', username='u', password='secret')
            self.assertEqual(context.destination, 'https://example.test/my.org/repo.git')

    def test_transport_config_is_environment_only_and_redirects_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.git'
            source.mkdir()
            (source / 'HEAD').write_text('ref: refs/heads/main\n')
            (source / 'objects').mkdir()
            with patch('git_isolated_offline._require_fixed_local_volume'):
                with IsolatedHttpsGit(source, 'https://example.test/repo.git', allowed_root=root,
                                      username='user', password='sensitive-token') as context:
                    with patch('git_isolated_offline.subprocess.run') as run:
                        run.return_value.returncode = 0
                        run.return_value.stdout = ''
                        self.assertIsNone(context.probe())
                        args, kw = run.call_args
                        self.assertNotIn('sensitive-token', str(args))
                        self.assertNotIn('sensitive-token', str(kw['env'].get('GIT_CONFIG_KEY_0')))
                        self.assertEqual(kw['env']['GIT_CONFIG_KEY_0'], 'http.extraHeader')
                        self.assertTrue(kw['env']['GIT_CONFIG_VALUE_0'].startswith('Authorization: Basic '))
                        self.assertIn('http.followRedirects=false', args[0])
                        self.assertIn('protocol.https.allow=always', args[0])
                        self.assertNotIn('GIT_TRACE', kw['env'])

    def test_push_uses_exact_lease_and_redacts_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source.git'
            source.mkdir()
            (source / 'HEAD').write_text('ref: refs/heads/main\n')
            (source / 'objects').mkdir()
            with patch('git_isolated_offline._require_fixed_local_volume'):
                with IsolatedHttpsGit(source, 'https://example.test/repo.git', allowed_root=tmp,
                                      username='user', password='sensitive-token') as context:
                    with patch('git_isolated_https.subprocess.run') as run:
                        run.return_value.returncode = 0
                        run.return_value.stdout = ''
                        context.push('a' * 40, expected='b' * 40)
                        command = run.call_args.args[0]
                        self.assertIn('--force-with-lease=refs/heads/game-channel:' + 'b' * 40, command)
                        run.return_value.returncode = 1
                        run.return_value.stderr = 'sensitive-token'
                        with self.assertRaises(RuntimeError) as error:
                            context.probe()
                        self.assertNotIn('sensitive-token', str(error.exception))


if __name__ == '__main__':
    unittest.main()
