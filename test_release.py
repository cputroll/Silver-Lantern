import hashlib
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class ReleaseTests(unittest.TestCase):
    def test_packaged_release_matches_updater_contract(self):
        import json
        version = json.loads((ROOT / 'sources.json').read_text(encoding='utf-8'))['tool']['version']
        subprocess.run(['powershell', '-NoProfile', '-File', str(ROOT / 'release.ps1'), '-Tag', 'v' + version, '-NoPublish'], cwd=ROOT, check=True)
        output = ROOT / 'dist-release'
        archive = output / 'HeyListen-Get.zip'
        sums = (output / 'SHA256SUMS.txt').read_text(encoding='utf-8-sig').splitlines()
        self.assertIn(hashlib.sha256(archive.read_bytes()).hexdigest() + '  HeyListen-Get.zip', sums)
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            self.assertEqual(names.count('HeyListen-Get.exe'), 1)
            exe_bytes = z.read('HeyListen-Get.exe')
            self.assertTrue(exe_bytes.startswith(b'MZ'))
            with tempfile.TemporaryDirectory() as temp:
                exe = Path(temp) / 'HeyListen-Get.exe'
                exe.write_bytes(exe_bytes)
                result = subprocess.run([str(exe), '--version', '--manifest', str(ROOT / 'sources.json')], capture_output=True, text=True, check=True)
                self.assertEqual(result.stdout.strip(), version)
            self.assertIn('SilverLantern-Get.cmd', names)
            self.assertIn('SilverLantern-Preset.cmd', names)
            # The game updater and watcher must ship beside the EXE (it looks for Update-Game.ps1 there).
            for name in ('Update-Game.ps1', 'Watch-Game.ps1', 'SilverLantern-AutoUpdate.cmd', 'Apply-HeyListen-Preset.ps1'):
                self.assertIn(name, names)


if __name__ == '__main__':
    unittest.main()
