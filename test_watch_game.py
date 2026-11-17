import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent


class WatcherFilesTest(unittest.TestCase):
    def test_watcher_and_launcher_exist(self):
        self.assertTrue((ROOT / 'Watch-Game.ps1').is_file())
        self.assertTrue((ROOT / 'SilverLantern-AutoUpdate.cmd').is_file())


if __name__ == '__main__':
    unittest.main()
