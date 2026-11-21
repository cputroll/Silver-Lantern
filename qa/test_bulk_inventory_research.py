import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

from install_scan_windows import PlainTree
from qa.bulk_inventory_research import read_inventory
from qa.benchmark_install_journal import main


@unittest.skipUnless(os.name == 'nt', 'requires Windows')
class BulkInventoryTests(unittest.TestCase):
    def test_benchmark_rejects_directory_swap_with_same_file_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            root = base / 'tree'
            sub = root / 'sub'
            sub.mkdir(parents=True)
            target = sub / 'file.bin'
            target.write_bytes(b'abc')
            manifest = base / 'manifest.json'
            payload = json.dumps({'files': [{'path': 'sub/file.bin', 'size': 3,
                                             'sha256': hashlib.sha256(b'abc').hexdigest()}]}).encode()
            manifest.write_bytes(payload)
            argv = ['benchmark', str(root), str(manifest), hashlib.sha256(payload).hexdigest(),
                    '--full-native', '--bulk-identities']

            swapped = False

            def swap_after_observation(tree, **kwargs):
                nonlocal swapped
                inventory = read_inventory(tree, **kwargs)
                if swapped:
                    return inventory
                swapped = True
                old = base / 'original'
                sub.rename(old)
                sub.mkdir()
                os.link(old / target.name, target)
                self.assertEqual(inventory, read_inventory(tree))  # File map alone is identical.
                return inventory

            with patch.object(sys, 'argv', argv), redirect_stdout(io.StringIO()), \
                    patch('qa.bulk_inventory_research.read_inventory', side_effect=swap_after_observation):
                with self.assertRaisesRegex(ValueError, 'Inventory changed during verification'):
                    main()

    def test_benchmark_checks_observed_identity_and_current_contents(self):
        for change in ('none', 'replacement', 'in-place', 'case-only', 'extra-file'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                base = Path(directory).resolve()
                root = base / 'tree'
                root.mkdir()
                target = root / 'ExactCase.bin'
                target.write_bytes(b'abc')
                manifest = base / 'manifest.json'
                payload = json.dumps({'files': [{'path': target.name, 'size': 3,
                                                 'sha256': hashlib.sha256(b'abc').hexdigest()}]}).encode()
                manifest.write_bytes(payload)
                argv = ['benchmark', str(root), str(manifest), hashlib.sha256(payload).hexdigest(),
                        '--full-native', '--bulk-identities']

                changed = False

                def observe_then_change(tree, **kwargs):
                    nonlocal changed
                    inventory = read_inventory(tree, **kwargs)
                    if changed:
                        return inventory
                    changed = True
                    if change == 'replacement':
                        target.rename(base / 'original.bin')
                        target.write_bytes(b'abc')
                    elif change == 'in-place':
                        with target.open('r+b') as stream:
                            stream.write(b'bad')
                    elif change == 'case-only':
                        intermediate = root / 'Intermediate.bin'
                        target.rename(intermediate)
                        intermediate.rename(root / 'exactcase.bin')
                    elif change == 'extra-file':
                        (root / 'Unexpected.bin').write_bytes(b'extra')
                    return inventory

                output = io.StringIO()
                with patch.object(sys, 'argv', argv), redirect_stdout(output), \
                        patch('qa.bulk_inventory_research.read_inventory', side_effect=observe_then_change):
                    if change == 'replacement':
                        with self.assertRaisesRegex(ValueError, 'File identity changed'):
                            main()
                    elif change in ('case-only', 'extra-file'):
                        with self.assertRaisesRegex(ValueError, 'Inventory changed during verification'):
                            main()
                    else:
                        errors = int(change == 'in-place')
                        self.assertEqual(main(), errors)
                        self.assertIn(f'ERRORS={errors} ', output.getvalue())

    def test_observed_identity_rejects_identical_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / 'ExactCase.bin'
            target.write_bytes(b'abc')
            with PlainTree(root) as tree:
                inventory = read_inventory(tree)
                self.assertEqual(list(inventory), ['ExactCase.bin'])
                size, identity = inventory['ExactCase.bin']
                self.assertEqual(size, 3)
                with tree.open('ExactCase.bin', expected_identity=identity) as stream:
                    self.assertEqual(stream.read(), b'abc')
                target.rename(root / 'Original.bin')
                target.write_bytes(b'abc')
                with self.assertRaisesRegex(ValueError, 'File identity changed'):
                    with tree.open('ExactCase.bin', expected_identity=identity):
                        self.fail('replacement was opened for reading')
                target.unlink()  # A failed identity check must not retain a handle.
