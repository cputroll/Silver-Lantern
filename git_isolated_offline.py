"""Unintegrated, local-only Git push/probe isolation prototype.

Not a production publisher: accepts only filesystem repositories beneath an
explicit trusted root. Windows fixed-volume classification does not prove
physical locality (for example, virtual disks may be network-backed). Callers
must trust the repositories, their metadata and parent directories, and prevent
modification throughout validation and use; this is not a hostile-filesystem sandbox.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile


class InvalidDestination(ValueError):
    pass


def _require_fixed_local_volume(path):
    """Fail closed when the actual containing Windows volume is not fixed."""
    if os.name != 'nt':
        raise InvalidDestination('local volume verification is only supported on Windows')
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.windll.kernel32
    volume_path = kernel.GetVolumePathNameW
    volume_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    volume_path.restype = wintypes.BOOL
    drive_type = kernel.GetDriveTypeW
    drive_type.argtypes = [wintypes.LPCWSTR]
    drive_type.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    if not volume_path(str(path), buffer, len(buffer)) or drive_type(buffer.value) != 3:
        raise InvalidDestination('destination must be on a fixed local volume')


class IsolatedGit:
    REF = 'refs/heads/game-channel'

    def __init__(self, source, destination, *, allowed_root):
        root = Path(allowed_root).resolve(strict=True)
        text = os.fspath(destination)
        if (not isinstance(text, str) or '\x00' in text or '://' in text or '::' in text
                or text.startswith(('\\\\', '//'))):
            raise InvalidDestination('destination must be a local repository')
        path = Path(text)
        if not path.is_absolute():
            raise InvalidDestination('destination must be absolute')
        try:
            self.destination = path.resolve(strict=True)
            if not self.destination.is_relative_to(root) or not self.destination.is_dir():
                raise InvalidDestination('destination outside trusted root')
            _require_fixed_local_volume(self.destination)
            # Only direct bare repositories: Git otherwise follows .git files or
            # directories, which can redirect the actual repository elsewhere.
            if (self.destination / '.git').exists() or (self.destination / '.git').is_symlink():
                raise InvalidDestination('destination must be a direct bare repository')
            if not (self.destination / 'HEAD').is_file() or not (self.destination / 'objects').is_dir():
                raise InvalidDestination('destination must be a direct bare repository')
            for name in ('HEAD', 'objects', 'refs', 'config', 'commondir'):
                entry = self.destination / name
                if entry.is_symlink() or (entry.exists() and not entry.resolve().is_relative_to(self.destination)):
                    raise InvalidDestination('repository indirection is not allowed')
            if (self.destination / 'commondir').exists():
                raise InvalidDestination('repository indirection is not allowed')
        except (OSError, RuntimeError) as error:
            raise InvalidDestination('invalid destination') from error
        try:
            source_path = Path(source).resolve(strict=True)
            if not source_path.is_relative_to(root) or not source_path.is_dir():
                raise ValueError('source outside trusted root')
            _require_fixed_local_volume(source_path)
            dotgit = source_path / '.git'
            if dotgit.is_symlink() or (dotgit.exists() and not dotgit.is_dir()):
                raise ValueError('source metadata indirection is not allowed')
            metadata = dotgit if dotgit.is_dir() else source_path
            if metadata.resolve(strict=True) != metadata or not metadata.is_relative_to(root):
                raise ValueError('source metadata indirection is not allowed')
            _require_fixed_local_volume(metadata)
            if not (metadata / 'HEAD').is_file() or not (metadata / 'objects').is_dir():
                raise ValueError('source must be a direct repository')
            for name in ('HEAD', 'objects', 'objects/info', 'objects/info/alternates',
                         'refs', 'config', 'commondir'):
                entry = metadata / name
                if entry.is_symlink() or (entry.exists() and entry.resolve() != entry):
                    raise ValueError('source metadata indirection is not allowed')
            if (metadata / 'commondir').exists() or (metadata / 'objects/info/alternates').exists():
                raise ValueError('source metadata indirection is not allowed')
            self.source = metadata
        except (OSError, RuntimeError) as error:
            raise ValueError('invalid source') from error
        self._temp = None

    def __enter__(self):
        temp_parent = Path(tempfile.gettempdir()).resolve(strict=True)
        _require_fixed_local_volume(temp_parent)
        self._temp = tempfile.TemporaryDirectory(prefix='isolated-git-', dir=temp_parent)
        try:
            temp_path = Path(self._temp.name).resolve(strict=True)
            _require_fixed_local_volume(temp_path)
        except Exception:
            self._temp.cleanup()
            self._temp = None
            raise
        self.repo = temp_path / 'private.git'
        self.env = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'WINDIR') if key in os.environ}
        self.env.update({'TEMP': str(temp_path), 'TMP': str(temp_path)})
        self.env.update({'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                         'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_REPLACE_OBJECTS': '1',
                         'HOME': self._temp.name, 'XDG_CONFIG_HOME': self._temp.name,
                         'LC_ALL': 'C'})
        try:
            self._run('init', '--bare', str(self.repo), repository=False)
        except Exception:
            self._temp.cleanup()
            self._temp = None
            raise
        return self

    def __exit__(self, *args):
        self._temp.cleanup()
        self._temp = None

    def _run(self, *args, repository=True):
        command = ['git']
        if repository:
            command += ['--git-dir', str(self.repo)]
        command += ['-c', 'protocol.allow=never', '-c', 'protocol.file.allow=always',
                    '-c', 'core.hooksPath=' + os.devnull, *args]
        try:
            result = subprocess.run(command, env=self.env, cwd=self._temp.name,
                                    capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError('isolated git operation failed') from None
        if result.returncode:
            raise RuntimeError('isolated git operation failed')
        return result.stdout

    @staticmethod
    def _oid(value):
        if not isinstance(value, str) or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', value):
            raise ValueError('invalid object id')
        return value

    def probe(self):
        output = self._run('ls-remote', '--refs', '--', str(self.destination), self.REF)
        if not output:
            return None
        lines = output.splitlines()
        if len(lines) != 1:
            raise RuntimeError('unexpected remote response')
        parts = lines[0].split('\t')
        if len(parts) != 2 or parts[1] != self.REF:
            raise RuntimeError('unexpected remote response')
        return self._oid(parts[0])

    def push(self, candidate, *, expected):
        candidate = self._oid(candidate)
        if expected is not None:
            expected = self._oid(expected)
        self._run('fetch', '--no-tags', '--', str(self.source), candidate)
        self._run('push', '--porcelain', '--force-with-lease=' + self.REF + ':' + (expected or ''),
                  '--', str(self.destination), candidate + ':' + self.REF)
