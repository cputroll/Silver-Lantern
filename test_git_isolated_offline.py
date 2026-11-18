"""Offline regression tests for the unintegrated Git isolation prototype."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from git_isolated_offline import IsolatedGit, InvalidDestination


def git(*args, cwd=None, env=None):
    return subprocess.run(['git', *map(str, args)], cwd=cwd, env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


class IsolatedGitTests(unittest.TestCase):
    def test_hostile_rewrites_do_not_redirect_push_or_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, target, decoy = (root / name for name in ('source', 'target.git', 'decoy.git'))
            git('init', source)
            git('config', 'user.name', 'Test', cwd=source)
            git('config', 'user.email', 'test@example.invalid', cwd=source)
            (source / 'file').write_text('content')
            git('add', 'file', cwd=source)
            git('commit', '-m', 'test', cwd=source)
            commit = git('rev-parse', 'HEAD', cwd=source)
            (source / 'file').write_text('new content')
            git('commit', '-am', 'second', cwd=source)
            second = git('rev-parse', 'HEAD', cwd=source)
            git('init', '--bare', target)
            git('init', '--bare', decoy)
            # Source-local, global, system and injected command-scope rewrites all
            # redirect the target if inherited by the isolated operations.
            git('config', f'url.{decoy.as_posix()}.pushInsteadOf', target.as_posix(), cwd=source)
            global_config = root / 'global'
            global_config.write_text(f'[url "{decoy.as_posix()}"]\n insteadOf = {target.as_posix()}\n pushInsteadOf = {target.as_posix()}\n')
            hostile = {'GIT_CONFIG_GLOBAL': str(global_config), 'GIT_CONFIG_SYSTEM': str(global_config),
                       'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': f'url.{decoy.as_posix()}.insteadOf',
                       'GIT_CONFIG_VALUE_0': target.as_posix(), 'GIT_DIR': str(decoy)}
            with patch.dict(os.environ, hostile):
                with IsolatedGit(source, target, allowed_root=root) as context:
                    self.assertIsNone(context.probe())
                    context.push(commit, expected=None)
                    self.assertEqual(context.probe(), commit)
                    with self.assertRaises(RuntimeError):
                        context.push(second, expected='0' * 40)
                    self.assertEqual(context.probe(), commit)
            self.assertEqual(git('--git-dir', target, 'rev-parse', 'refs/heads/game-channel'), commit)
            self.assertNotEqual(subprocess.run(['git', '--git-dir', str(decoy), 'rev-parse', '--verify', 'refs/heads/game-channel'], capture_output=True).returncode, 0)

    def test_rejects_nonlocal_or_outside_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            for target in ('https://user:secret@example.com/repo', '../escape', 'ext::danger'):
                with self.subTest(target=target), self.assertRaises(InvalidDestination):
                    IsolatedGit(tmp, target, allowed_root=tmp)

    def test_rejects_gitfile_indirection(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            destination = root / 'destination'
            destination.mkdir()
            git('init', '--bare', Path(outside) / 'external.git')
            (destination / '.git').write_text(f'gitdir: {Path(outside) / "external.git"}\n')
            with self.assertRaises(InvalidDestination):
                IsolatedGit(root, destination, allowed_root=root)

    @unittest.skipUnless(os.name == 'nt', 'Windows drive classification')
    def test_rejects_remote_and_unknown_drive_types(self):
        import ctypes
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git('init', '--bare', root / 'target')
            for drive_type in (0, 1, 4):
                with self.subTest(drive_type=drive_type), patch.object(ctypes.windll.kernel32, 'GetDriveTypeW', return_value=drive_type):
                    with self.assertRaises(InvalidDestination):
                        IsolatedGit(root, root / 'target', allowed_root=root)

    def test_rejects_unc_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(InvalidDestination):
                IsolatedGit(tmp, r'\\\\server\\share\\repo', allowed_root=tmp)

    def test_rejects_source_gitfile_indirection(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            source = root / 'source'
            source.mkdir()
            git('init', '--bare', Path(outside) / 'outside.git')
            (source / '.git').write_text(f'gitdir: {Path(outside) / "outside.git"}\n')
            git('init', '--bare', root / 'target')
            with self.assertRaises(ValueError):
                IsolatedGit(source, root / 'target', allowed_root=root)

    @unittest.skipUnless(os.name == 'nt', 'Windows junctions')
    def test_rejects_source_junction_outside_root(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            git('init', Path(outside) / 'source')
            git('init', '--bare', root / 'target')
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(root / 'source'), str(Path(outside) / 'source')],
                           check=True, capture_output=True)
            with self.assertRaises(ValueError):
                IsolatedGit(root / 'source', root / 'target', allowed_root=root)

    def test_rejects_source_git_alternates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git('init', root / 'source')
            git('init', '--bare', root / 'target')
            (root / 'source' / '.git' / 'objects' / 'info' / 'alternates').write_text('../outside')
            with self.assertRaises(ValueError):
                IsolatedGit(root / 'source', root / 'target', allowed_root=root)

    def test_rejects_unverified_temporary_volume_before_git_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git('init', root / 'source')
            git('init', '--bare', root / 'target')
            context = IsolatedGit(root / 'source', root / 'target', allowed_root=root)
            with patch('git_isolated_offline._require_fixed_local_volume', side_effect=ValueError('unverified')), \
                 patch.object(context, '_run') as run:
                with self.assertRaises(ValueError):
                    context.__enter__()
                run.assert_not_called()

    def test_rejects_source_unverified_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git('init', root / 'source')
            git('init', '--bare', root / 'target')
            with patch('git_isolated_offline._require_fixed_local_volume', side_effect=lambda p: (_ for _ in ()).throw(ValueError('unverified')) if Path(p) == (root / 'source').resolve() else None):
                with self.assertRaises(ValueError):
                    IsolatedGit(root / 'source', root / 'target', allowed_root=root)

    def test_rejects_bad_object_ids_without_running_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git('init', '--bare', root / 'source')
            git('init', '--bare', root / 'target')
            with IsolatedGit(root / 'source', root / 'target', allowed_root=root) as context:
                with self.assertRaises(ValueError):
                    context.push('secret', expected=None)


if __name__ == '__main__':
    unittest.main()
