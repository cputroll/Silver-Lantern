"""Conservative NTFS change-journal replay for installed-file verification."""

import ctypes
import hashlib
import os
import stat
import struct
import time
from ctypes import wintypes
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class _Baseline:
    manifest: str
    root: tuple
    journal: int
    cursor: int
    ids: frozenset


def _identity(info):
    if (not info.st_ino or getattr(info, 'st_file_attributes', 0) & 0x400):
        raise OSError('Untrackable file identity')
    return info.st_dev, info.st_ino


def _root_identity(root):
    if not root.is_absolute():
        raise OSError('Install root must be absolute')
    for parent in root.parents:
        _identity(parent.lstat())
    return _identity(root.lstat())


def build_baseline(root, manifest, files):
    """Create an in-memory baseline only after a full, stable content check."""
    try:
        with VolumeJournal(root) as journal:
            jid, _, start = journal.bounds()
            device, root_id = _root_identity(root)
            directories = {(): root_id}
            ids = {root_id}
            for entry in files:
                parts = tuple(entry['path'].split('/'))
                if any(not part or part in ('.', '..') or part[-1] in ' .'
                       or any(char in part for char in '\\:<>"|?*\0') for part in parts):
                    return None
                for depth in range(1, len(parts)):
                    prefix = parts[:depth]
                    if prefix not in directories:
                        info = root.joinpath(*prefix).lstat()
                        volume, file_id = _identity(info)
                        if volume != device or not stat.S_ISDIR(info.st_mode):
                            return None
                        directories[prefix] = file_id
                        ids.add(file_id)
                path = root.joinpath(*parts)
                with open_baseline_file(path) as stream:
                    info = os.fstat(stream.fileno())
                    volume, file_id = _identity(info)
                    if (volume != device or not stat.S_ISREG(info.st_mode)
                            or info.st_size != entry['size'] or _identity(path.lstat()) != (volume, file_id)):
                        return None
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != entry['sha256']:
                        return None
                    ids.add(file_id)
            latest, first, end = journal.bounds()
            if (latest != jid or not first <= start <= end
                    or _root_identity(root) != (device, root_id)
                    or not unchanged(journal.reader(jid), start, end, ids)):
                return None
            return _Baseline(manifest, (device, root_id), jid, end, frozenset(ids))
    except (OSError, ValueError):
        return None


def verify_baseline(root, manifest, receipt):
    if type(receipt) is not _Baseline:
        return False
    try:
        with VolumeJournal(root) as journal:
            root_id = _root_identity(root)
            bounds = journal.bounds()
            identity, first, end = bounds
            if not (receipt.manifest == manifest and receipt.root == root_id and receipt.journal == identity
                    and first <= receipt.cursor <= end
                    and unchanged(journal.reader(identity), receipt.cursor, end, receipt.ids)):
                return False
            # Refuse if we lost journal coverage during the read itself.
            identity, first, _ = journal.bounds()
            return identity == bounds[0] and first <= receipt.cursor and _root_identity(root) == root_id
    except (OSError, ValueError):
        return False


def open_baseline_file(path):
    import msvcrt

    w = wintypes
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateFileW.argtypes = (w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE)
    k.CreateFileW.restype = w.HANDLE
    k.CloseHandle.argtypes = (w.HANDLE,)
    handle = k.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x08200000, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        k.CloseHandle(handle)
        raise
    return os.fdopen(fd, 'rb')


class VolumeJournal:
    def __init__(self, root):
        self.root = root
        self.handle = None

    def __enter__(self):
        if os.name != 'nt':
            raise OSError('NTFS journal requires Windows')
        volume = self.root.resolve(strict=True).anchor
        if len(volume) != 3 or volume[1:] != ':\\':
            raise OSError('NTFS journal requires a local drive')
        w = wintypes
        k = self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        k.CreateFileW.argtypes = (w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE)
        k.CreateFileW.restype = w.HANDLE
        k.DeviceIoControl.argtypes = (w.HANDLE, w.DWORD, w.LPVOID, w.DWORD, w.LPVOID, w.DWORD,
                                      ctypes.POINTER(w.DWORD), w.LPVOID)
        k.CloseHandle.argtypes = (w.HANDLE,)
        handle = k.CreateFileW(volume, 0, 7, None, 3, 0x02000000, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())
        self.handle = handle
        return self

    def __exit__(self, *_):
        self.kernel.CloseHandle(self.handle)
        self.handle = None

    def _ioctl(self, code, data=b''):
        source = ctypes.create_string_buffer(data) if data else None
        result = ctypes.create_string_buffer(65536)
        size = wintypes.DWORD()
        if not self.kernel.DeviceIoControl(self.handle, code, source, len(data), result,
                                           len(result), ctypes.byref(size), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return result.raw[:size.value]

    def bounds(self):
        data = self._ioctl(0x900F4)  # FSCTL_QUERY_USN_JOURNAL
        if len(data) < 32:
            raise OSError('Incomplete NTFS journal bounds')
        identity, first, end, lowest = struct.unpack_from('<Qqqq', data)
        return identity, max(first, lowest), end

    def reader(self, identity):
        def read(cursor):
            request = struct.pack('<qIIQQQHHI', cursor, 0xFFFFFFFF, 0, 0, 0, identity, 2, 3, 0)
            return self._ioctl(0x903AB, request)  # FSCTL_READ_UNPRIVILEGED_USN_JOURNAL
        return read


def receipt_matches(receipt, manifest, root, bounds, read):
    """Validate a baseline supplied by a trusted caller, not arbitrary on-disk JSON."""
    identity, first, end = bounds
    if not isinstance(receipt, dict):
        return False
    ids = receipt.get('ids')
    start = receipt.get('cursor')
    if (receipt.get('manifest') != manifest or receipt.get('root') != list(root)
            or receipt.get('journal') != identity or type(start) is not int
            or not first <= start <= end or not isinstance(ids, list)
            or any(type(value) is not int or value <= 0 for value in ids)):
        return False
    tracked = set(ids)
    if root[1] not in tracked:
        return False
    return unchanged(read, start, end, tracked)


def unchanged(read, start, end, tracked, *, budget=3.0):
    """Accept only a complete, bounded replay with no relevant record."""
    if not 0 <= start <= end:
        return False
    deadline = time.monotonic() + budget
    cursor = start
    while cursor < end:
        if time.monotonic() >= deadline:
            return False
        try:
            block = read(cursor)
        except OSError:
            return False
        if len(block) < 8:
            return False
        following, = struct.unpack_from('<q', block)
        if following <= cursor:
            return False
        offset = 8
        last = cursor - 1
        while offset < len(block):
            if len(block) - offset < 8:
                return False
            length, major, minor = struct.unpack_from('<IHH', block, offset)
            if minor or major not in (2, 3):
                return False
            minimum = 60 if major == 2 else 76
            if length < minimum or length % 8 or length > len(block) - offset:
                return False
            width = 8 if major == 2 else 16
            ids = offset + 8
            file_id = int.from_bytes(block[ids:ids + width], 'little')
            parent_id = int.from_bytes(block[ids + width:ids + 2 * width], 'little')
            usn, = struct.unpack_from('<q', block, ids + 2 * width)
            if not last < usn < following:
                return False
            if usn < end and (file_id in tracked or parent_id in tracked):
                return False
            last = usn
            offset += length
        cursor = following
    return time.monotonic() < deadline
