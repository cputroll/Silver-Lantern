import hashlib
import shutil
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
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        root = Path(workspace.name) / 'repo'
        shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns('.git', 'dist-release', '__pycache__', '.pytest_cache', 'shots', 'Microsoft'))
        sdl_cache = ROOT / 'dist-release' / 'SDL2-2.30.10'
        if sdl_cache.is_dir():
            shutil.copytree(sdl_cache, root / 'dist-release' / sdl_cache.name)
        subprocess.run(['powershell', '-NoProfile', '-File', str(root / 'release.ps1'), '-Tag', 'v' + version, '-NoPublish'], cwd=root, check=True, timeout=600)
        output = root / 'dist-release'
        archive = output / 'HeyListen-Get.zip'
        sums = (output / 'SHA256SUMS.txt').read_text(encoding='utf-8-sig').splitlines()
        self.assertIn(hashlib.sha256(archive.read_bytes()).hexdigest() + '  HeyListen-Get.zip', sums)
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            self.assertEqual({n for n in names if '/' not in n}, {'Run Silver Lantern.exe', 'Read me.txt'})
            exe_bytes = z.read('Run Silver Lantern.exe')
            self.assertEqual(z.read('support/SilverLantern.exe'), exe_bytes)
            self.assertEqual(z.read('support/HeyListen-Get.exe'), exe_bytes)
            self.assertEqual(len(z.read('Read me.txt').decode('utf-8').splitlines()), 2)
            self.assertTrue(exe_bytes.startswith(b'MZ'))
            import struct
            pe = struct.unpack_from('<I', exe_bytes, 0x3c)[0]
            self.assertEqual(struct.unpack_from('<H', exe_bytes, pe + 4 + 20 + 68)[0], 2)
            with tempfile.TemporaryDirectory() as temp:
                exe = Path(temp) / 'SilverLantern.exe'
                exe.write_bytes(exe_bytes)
                result = subprocess.run([str(exe), '--version', '--manifest', str(ROOT / 'sources.json')], capture_output=True, text=True, check=True, timeout=60)
                self.assertEqual(result.stdout.strip(), version)
                result = subprocess.run([str(exe), '--cli', '--version'], capture_output=True, text=True, check=True, timeout=60)
                self.assertEqual(result.stdout.strip(), version)
                subprocess.run([str(exe), '--self-test'], capture_output=True, text=True, check=True, timeout=60)
            for name in ('SilverLantern-Get.cmd', 'SilverLantern-Preset.cmd', 'Update-Game.ps1', 'Watch-Game.ps1', 'SilverLantern-AutoUpdate.cmd', 'Apply-HeyListen-Preset.ps1', 'sources.json', 'SDL2.dll', 'README.md'):
                self.assertIn('support/' + name, names)


if __name__ == '__main__':
    unittest.main()
