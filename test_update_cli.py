import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location('heylisten_cli_update', Path(__file__).with_name('Get-HeyListen.py'))
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class UpdateCliTests(unittest.TestCase):
    def test_parse_protocol(self):
        self.assertEqual(cli.parse_update_line('HLUPDATE installed=v1'), {'installed': 'v1'})
        self.assertEqual(cli.parse_update_line('HLUPDATE times total=12 compile=2'), {'total': '12', 'compile': '2'})
        self.assertEqual(cli.parse_update_line('garbage'), {})
        self.assertEqual(cli.parse_update_line('HLUPDATE nonsense'), {})
        self.assertEqual(cli.parse_update_line('HLUPDATE future=value'), {'future': 'value'})
        cases = [
            ('HLUPDATE status=current installed=v0.4.0 latest=v0.4.0', {'status': 'current', 'installed': 'v0.4.0', 'latest': 'v0.4.0'}),
            ('HLUPDATE status=available installed=v0.4.0 latest=v0.5.0', {'status': 'available', 'installed': 'v0.4.0', 'latest': 'v0.5.0'}),
            ('HLUPDATE status=updated from=v0.4.0 installed=v0.4.0 to=v0.5.0 latest=v0.5.0 seconds=12', {'status': 'updated', 'from': 'v0.4.0', 'installed': 'v0.4.0', 'to': 'v0.5.0', 'latest': 'v0.5.0', 'seconds': '12'}),
            ('HLUPDATE status=error reason=builder-running', {'status': 'error', 'reason': 'builder-running'}),
            ('HLUPDATE garbage', {}),
        ]
        for line, expected in cases:
            with self.subTest(line=line):
                self.assertEqual(cli.parse_update_line(line), expected)

    def test_friendly_statuses(self):
        fields = {'installed': 'v1', 'latest': 'v2', 'total': '12', 'error': 'bad file'}
        for status, expected in [('current', 'Already up to date (v1).'), ('available', 'Update available: v1 -> v2 (dry run).'), ('updated', 'Updated v1 -> v2 in 12s.'), ('error', 'Update failed: bad file')]:
            with self.subTest(status=status):
                self.assertEqual(cli.render_update_status(dict(fields, status=status)), expected)

    def test_exit_mapping(self):
        for code, expected in [(0, 0), (10, 0), (1, 1), (99, 1)]:
            self.assertEqual(cli.update_exit_code(code), expected)

    def test_missing_script(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cli, 'HERE', Path(d)), mock.patch.object(cli.Path, 'cwd', return_value=Path(d)), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.run_game_update(Path(d)), 1)
            self.assertIn('not found', out.getvalue().lower())

    def test_subprocess_stream_and_log(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'Update-Game.ps1').touch()
            process = mock.Mock(stdout=iter(['private build output\n', 'HLUPDATE installed=v1\n', 'HLUPDATE status=up-to-date\n']))
            process.wait.return_value = 0
            with mock.patch.object(cli, 'HERE', root), mock.patch.object(cli, 'UPDATE_LOG', root / 'update.log'), mock.patch.object(cli.subprocess, 'Popen', return_value=process) as popen, contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(cli.run_game_update(root, dry_run=True, force=True), 0)
            self.assertIn('Already up to date (v1).', out.getvalue())
            self.assertNotIn('private build output', out.getvalue())
            self.assertIn('private build output', (root / 'update.log').read_text())
            self.assertIn('-DryRun', popen.call_args.args[0])
            self.assertIn('-Force', popen.call_args.args[0])

    def test_on_launch_failure_is_nonfatal(self):
        with mock.patch.object(sys, 'argv', ['tool', '--update-on-launch', '--no-self-update', '--no-launch', '--no-preset']), mock.patch.object(cli, 'load_manifest', return_value={'game': {}, 'tool': {}}), mock.patch.object(cli, 'session'), mock.patch.object(cli, 'download_builder'), mock.patch.object(cli, 'download_game'), mock.patch.object(cli, 'run_game_update', return_value=1) as update, mock.patch.object(cli.Path, 'mkdir'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(), 0)
            update.assert_called_once()

    def test_update_mode_uses_default_and_flags(self):
        with mock.patch.object(sys, 'argv', ['tool', '--update-game', '--update-dry-run', '--force-update']), mock.patch.object(cli, 'run_game_update', return_value=0) as update:
            self.assertEqual(cli.main(), 0)
            self.assertEqual(update.call_args.args[0], Path(cli.default_game_folder()))
            self.assertTrue(update.call_args.kwargs['dry_run'])
            self.assertTrue(update.call_args.kwargs['force'])
