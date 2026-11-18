import subprocess
import tempfile
import unittest
from pathlib import Path


class BuildFlagsTests(unittest.TestCase):
    def test_flag_generation_and_artifact_names(self):
        helper = Path(__file__).with_name('build-flags.ps1')
        with tempfile.TemporaryDirectory() as d:
            for qa, expected, artifact in [(False, 'False', 'HeyListen-Get.zip'), (True, 'True', 'HeyListen-Get-qa.zip')]:
                script = f". '{helper}'; Write-BuildFlags -Directory '{d}' -Qa:${str(qa).lower()}; Get-BuildArtifactName -Qa:${str(qa).lower()}"
                result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True, text=True, check=True)
                self.assertEqual(result.stdout.strip(), artifact)
                self.assertEqual((Path(d) / 'build_flags.py').read_text(), f'QA_SEAMS = {expected}\n')
