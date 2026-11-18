import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('packer_exclusions', Path(__file__).with_name('pack-game.py'))
packer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packer)


class PackExclusionTests(unittest.TestCase):
    def test_live_folder_artifacts_excluded_and_settings_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'game'; source.mkdir()
            excluded = ['.heylisten-get/state', '.heylisten-parts/bundle', '.heylisten-new123',
                        'nested/file.heylisten-new', 'nested/file.update-backup', 'HeyListen-Get.log',
                        'launcher.log', 'SilverLantern.exe', 'HeyListen-Get.exe',
                        'Run Silver Lantern.exe', 'support/SilverLantern.exe',
                        'support/HeyListen-Get.exe', 'hookshot_options.ini']
            included = ['game.dat', 'nested/SilverLantern.exe', 'Ocarina of Time Test.exe',
                        'Run Silver Lantern x.exe', 'SilverLantern-v2.exe', 'Ship of Harkinian.exe']
            for name in excluded + included:
                path = source / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(name.encode())
            result = packer.pack(source, root / 'out')
            self.assertEqual(set(f['path'] for f in result['files']), set(included))
            result = packer.pack(source, root / 'out2', include_settings=True)
            self.assertEqual(set(f['path'] for f in result['files']), set(included + ['hookshot_options.ini']))
