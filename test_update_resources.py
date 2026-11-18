import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import launcher

spec = importlib.util.spec_from_file_location('update_resource_cli', Path(__file__).with_name('Get-HeyListen.py'))
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class UpdateResourceTests(unittest.TestCase):
    def test_frozen_resolver_uses_bundle_not_untrusted_neighbors(self):
        import update_resources
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            bundle = root / 'bundle'
            bundle.mkdir()
            (bundle / 'Update-Game.ps1').touch()
            (root / 'Update-Game.ps1').touch()
            with mock.patch.object(update_resources.sys, 'frozen', True, create=True), mock.patch.object(update_resources.sys, '_MEIPASS', str(bundle), create=True):
                self.assertEqual(update_resources.resolve_update_script(root), bundle / 'Update-Game.ps1')
                (bundle / 'Update-Game.ps1').unlink()
                self.assertIsNone(update_resources.resolve_update_script(root))

    def test_source_resolver_uses_support_but_rejects_current_directory(self):
        import update_resources
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            support = root / 'support'
            support.mkdir()
            script = support / 'Update-Game.ps1'
            script.touch()
            with mock.patch.object(update_resources.sys, 'frozen', False, create=True):
                self.assertEqual(update_resources.resolve_update_script(root), script)
                script.unlink()
                script = root / 'cwd' / 'Update-Game.ps1'
                script.parent.mkdir()
                script.touch()
                with mock.patch.object(update_resources.Path, 'cwd', return_value=script.parent):
                    self.assertIsNone(update_resources.resolve_update_script(root))

    def test_both_entry_points_share_resolver_and_release_embeds_pair(self):
        import update_resources
        self.assertIs(cli.resolve_update_script, update_resources.resolve_update_script)
        self.assertIs(launcher.resolve_update_script, update_resources.resolve_update_script)
        release = Path(__file__).with_name('release.ps1').read_text()
        build = next(line for line in release.splitlines() if 'python -m PyInstaller' in line)
        for script in ('Update-Game.ps1', 'Apply-HeyListen-Preset.ps1'):
            self.assertIn(script, build)
