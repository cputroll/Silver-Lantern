import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_sync import h
from test_game_v2 import sample


def make_junction(source: Path, junction: Path):
    import _winapi
    _winapi.CreateJunction(str(source), str(junction))


class VerifyWriteBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'game'
        self.root.mkdir()

    def test_plain_chain_passes(self):
        target = self.root / 'romfs' / 'a' / 'file.zs'
        h.verify_write_boundary(self.root, target, 'romfs/a/file.zs')

    def test_junction_parent_refused_and_referent_untouched(self):
        if os.name != 'nt':
            self.skipTest('junctions require Windows')
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        sentinel = outside / 'sentinel.bin'
        sentinel.write_bytes(b'safe')
        make_junction(outside, self.root / 'romfs')
        with self.assertRaises(h.InvalidGamePath):
            h.verify_write_boundary(self.root, self.root / 'romfs' / 'file.zs', 'romfs/file.zs')
        self.assertEqual(sentinel.read_bytes(), b'safe')

    def test_reparse_final_target_refused(self):
        if os.name != 'nt':
            self.skipTest('junctions require Windows')
        outside = Path(self.tmp.name) / 'out2'
        outside.mkdir()
        (self.root / 'bin').mkdir()
        make_junction(outside, self.root / 'bin' / 'game.exe')
        with self.assertRaises(h.InvalidGamePath):
            h.verify_write_boundary(self.root, self.root / 'bin' / 'game.exe', 'bin/game.exe')

    def test_containment_survives_prefix_and_case_forms(self):
        root = self.root
        target = root / 'Bin' / 'Game.exe'
        resolved = h._comparable_resolved_path(Path('\\\\?\\' + str(target.resolve())))
        self.assertTrue(resolved.is_relative_to(h._comparable_resolved_path(root.resolve())))
        unc = h._comparable_resolved_path(Path('\\\\?\\UNC\\server\\share\\x'))
        self.assertEqual(str(unc), '\\\\server\\share\\x')

    def test_target_outside_root_refused(self):
        other = Path(self.tmp.name) / 'other'
        other.mkdir()
        with self.assertRaises(h.InvalidGamePath):
            h.verify_write_boundary(self.root, other / 'x.bin', 'x.bin')


class JunctionSwapDuringAssemblyTests(unittest.TestCase):
    def setUp(self):
        disk = patch.object(h.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 100 * 1024 ** 3})())
        disk.start(); self.addCleanup(disk.stop)
        running = patch.object(h, 'running_launcher_paths', return_value=set())
        running.start(); self.addCleanup(running.stop)

    def test_swapped_parent_directory_refused_before_write(self):
        if os.name != 'nt':
            self.skipTest('junctions require Windows')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'game'
            root.mkdir()
            outside = Path(d) / 'outside'
            outside.mkdir()
            sentinel = outside / 'game.exe'
            sentinel.write_bytes(b'safe')
            result = {}
            gate = threading.Event()
            dump = Path(d) / 'swap-failure-traceback.txt'

            def install():
                def stream(s, url, dest, **kw):
                    name = url.rsplit('/', 1)[-1]
                    data = {'bundle-0.tar': b'xxabc!', 'bundle-1.tar': b'?defyy'}[name]
                    if name == 'bundle-1.tar':
                        # Hold the second bundle so segment 0 finishes, its
                        # staging handle closes, and the swap window is open.
                        gate.wait(timeout=30)
                    dest.write_bytes(data); kw['progress'](len(data))
                m = sample()
                try:
                    with patch.object(h, 'stream_to', side_effect=stream):
                        h.install_game(object(), h.parse_game_manifest(json.dumps(m)), root)
                    result['ok'] = True
                except Exception as exc:
                    result['exc'] = exc
                    import traceback
                    dump.write_text(traceback.format_exc(), encoding='utf-8')

            thread = threading.Thread(target=install)
            thread.start()
            try:
                # Swap bin/ for a junction while segment 1 is gated: the
                # staging handle is closed, so the rename always succeeds.
                bin_dir = root / 'bin'
                deadline = time.monotonic() + 20
                swapped = False
                while time.monotonic() < deadline and not swapped:
                    if (bin_dir / 'game.exe.heylisten-new').exists():
                        try:
                            bin_dir.rename(root / 'bin.moved')
                            make_junction(outside, bin_dir)
                            swapped = True
                            break
                        except OSError:
                            time.sleep(0.01)
                    else:
                        time.sleep(0.01)
                self.assertTrue(swapped, 'could not stage the junction swap')
            finally:
                gate.set()
                thread.join(timeout=60)
            self.assertNotIn('ok', result)
            self.assertIsInstance(result.get('exc'), h.InvalidGamePath,
                                   f'see {dump}: {result.get("exc")!r}')
            self.assertEqual(sentinel.read_bytes(), b'safe')


class V1LegacyBoundaryWiringTests(unittest.TestCase):
    def setUp(self):
        disk = patch.object(h.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 100 * 1024 ** 3})())
        disk.start(); self.addCleanup(disk.stop)
        running = patch.object(h, 'running_launcher_paths', return_value=set())
        running.start(); self.addCleanup(running.stop)

    def test_v1_install_rechecks_the_write_boundary(self):
        from test_game_sync import manifest
        calls = []
        original = h.verify_write_boundary

        def recording(root, target, entry):
            calls.append(entry)
            return original(root, target, entry)

        def stream(s, url, dest, **kw):
            dest.write_bytes(b'abc'); kw['progress'](3)

        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'game'
            with patch.object(h, 'verify_write_boundary', side_effect=recording), \
                 patch.object(h, 'stream_to', side_effect=stream):
                h.install_game(object(), h.parse_game_manifest(json.dumps(manifest())), root)
            self.assertEqual((root / 'bin' / 'game.exe').read_bytes(), b'abc')
        self.assertEqual(calls, ['bin/game.exe', 'bin/game.exe'],
                         'v1 installs must recheck the boundary before staging and before final placement')


if __name__ == '__main__':
    unittest.main()
