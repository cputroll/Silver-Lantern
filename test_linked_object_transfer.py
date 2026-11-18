import os
import subprocess
from pathlib import Path

import pytest

from linked_object_transfer import transfer_commit


def git(*args, cwd=None):
    return subprocess.run(['git', *map(str, args)], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def test_transfers_linked_worktree_commit_without_following_dotgit(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    main = root / 'main'
    git('init', main)
    git('-C', main, 'config', 'user.email', 'test@example.com')
    git('-C', main, 'config', 'user.name', 'Test')
    (main / 'file').write_text('content')
    git('-C', main, 'add', 'file')
    git('-C', main, 'commit', '-m', 'first')
    linked = root / 'linked'
    git('-C', main, 'worktree', 'add', '-b', 'linked', linked)
    (linked / 'file').write_text('linked content')
    git('-C', linked, 'commit', '-am', 'linked')
    oid = git('-C', linked, 'rev-parse', 'HEAD')
    target = root / 'isolated.git'
    git('init', '--bare', target)
    dotgit = linked / '.git'
    dotgit.unlink()
    dotgit.write_text('gitdir: /not/a/trusted/repository\n')
    assert dotgit.read_text() == 'gitdir: /not/a/trusted/repository\n'
    transfer_commit(main / '.git' / 'objects', target, oid, trusted_root=root)
    assert git('--git-dir', target, 'show', f'{oid}:file') == 'linked content'
    assert git('--git-dir', target, 'fsck', '--no-reflogs', oid) == ''


def test_rejects_alternates_and_outside_root(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    source = root / 'source.git'
    target = root / 'target.git'
    git('init', '--bare', source)
    git('init', '--bare', target)
    alt = source / 'objects' / 'info' / 'alternates'
    alt.write_text(str(tmp_path / 'external'))
    with pytest.raises(ValueError):
        transfer_commit(source / 'objects', target, 'a' * 40, trusted_root=root)
    with pytest.raises(ValueError):
        transfer_commit(tmp_path, target, 'a' * 40, trusted_root=root)
