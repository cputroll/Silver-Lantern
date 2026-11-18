"""Offline object transfer prototype; not connected to publishing.

The caller must exclusively control the trusted root, source objects, target bare
repository, Git executable and their parents for the whole operation. This is
not a sandbox against concurrent filesystem changes, malicious Git objects, or
untrusted executables. Pass the common object directory explicitly: the linked
worktree .git file is not consulted. Git may read target repository config; its
metadata must remain trusted. The target must be a freshly created, trusted
isolated bare repository. This does not prove candidate approval or physical locality.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile

from git_isolated_offline import _require_fixed_local_volume


def _contained(path, root):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('absolute path required')
    resolved = path.resolve(strict=True)
    if resolved != path or not resolved.is_relative_to(root) or not resolved.is_dir():
        raise ValueError('path must be a direct directory within trusted root')
    _require_fixed_local_volume(resolved)
    return resolved


def transfer_commit(objects, target, oid, *, trusted_root):
    if not isinstance(oid, str) or not re.fullmatch(r'[0-9a-f]{40}', oid):
        raise ValueError('expected a full SHA-1 commit id')
    root = Path(trusted_root).resolve(strict=True)
    objects = _contained(objects, root)
    target = _contained(target, root)
    if objects == target or objects.is_relative_to(target) or target.is_relative_to(objects):
        raise ValueError('source and destination must be separate')
    for entry in (objects / 'info', objects / 'info' / 'alternates',
                  objects / 'info' / 'http-alternates', objects / 'pack'):
        if entry.is_symlink() or (entry.exists() and entry.resolve() != entry):
            raise ValueError('object directory indirection forbidden')
    if (objects / 'info' / 'alternates').exists() or (objects / 'info' / 'http-alternates').exists():
        raise ValueError('object alternates forbidden')
    if ((target / '.git').exists() or (target / '.git').is_symlink()
            or not (target / 'HEAD').is_file() or not (target / 'objects').is_dir()):
        raise ValueError('target must be a direct bare repository')
    for name in ('HEAD', 'objects', 'refs', 'config', 'commondir', 'objects/info/alternates'):
        entry = target / name
        if entry.is_symlink() or (entry.exists() and entry.resolve() != entry):
            raise ValueError('target indirection forbidden')
    if (target / 'commondir').exists() or (target / 'objects/info/alternates').exists():
        raise ValueError('target indirection forbidden')

    with tempfile.TemporaryDirectory(prefix='object-transfer-') as temp:
        _require_fixed_local_volume(Path(temp))
        env = {k: os.environ[k] for k in ('PATH', 'SYSTEMROOT', 'WINDIR') if k in os.environ}
        env.update({'HOME': temp, 'XDG_CONFIG_HOME': temp, 'TEMP': temp, 'TMP': temp,
                    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                    'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_REPLACE_OBJECTS': '1',
                    'GIT_NO_LAZY_FETCH': '1', 'LC_ALL': 'C'})
        repo = Path(temp) / 'source.git'
        common = ['git', '-c', 'protocol.allow=never', '-c', 'core.hooksPath=' + os.devnull]

        def run(args, **kwargs):
            try:
                subprocess.run(common + args, cwd=temp, env=kwargs.pop('env', env),
                               check=True, stderr=subprocess.PIPE, timeout=60, **kwargs)
            except (OSError, subprocess.SubprocessError):
                raise RuntimeError('offline object transfer failed') from None

        run(['init', '--bare', str(repo)], stdout=subprocess.DEVNULL)
        source_env = dict(env, GIT_OBJECT_DIRECTORY=str(objects))
        run(['--git-dir', str(repo), 'cat-file', '-e', oid + '^{commit}'], env=source_env,
            stdout=subprocess.DEVNULL)
        pack = Path(temp) / 'objects.pack'
        with pack.open('wb') as stream:
            run(['--git-dir', str(repo), 'pack-objects', '--stdout', '--revs'],
                env=source_env, input=(oid + '\n').encode('ascii'), stdout=stream)
        with pack.open('rb') as stream:
            run(['--git-dir', str(target), 'index-pack', '--stdin', '--strict'],
                stdin=stream, stdout=subprocess.DEVNULL)
