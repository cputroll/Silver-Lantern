"""Experimental Windows file reads relative to a pinned directory handle."""

import ctypes as c
import os
import re
from ctypes import wintypes as w

_SPECIAL = re.compile(r'[<>"|?*\x00-\x1f\\:]|[. ]$|^(?:CON|PRN|AUX|NUL|(?:COM|LPT)[1-9¹²³]) *(?:\.|$)', re.I)


class UnicodeString(c.Structure):
    _fields_ = [('Length', w.USHORT), ('MaximumLength', w.USHORT), ('Buffer', w.LPWSTR)]


class ObjectAttributes(c.Structure):
    _fields_ = [('Length', w.ULONG), ('RootDirectory', w.HANDLE),
                ('ObjectName', c.POINTER(UnicodeString)), ('Attributes', w.ULONG),
                ('SecurityDescriptor', w.LPVOID), ('SecurityQualityOfService', w.LPVOID)]


class IoStatus(c.Structure):
    _fields_ = [('Status', c.c_void_p), ('Information', c.c_size_t)]


class FileIdInfo(c.Structure):
    _fields_ = [('volume_serial', c.c_uint64), ('file_id', c.c_ubyte * 16)]


class PlainTree:
    def __init__(self, root):
        self.root = root
        self.handle = None

    def __enter__(self):
        anchor = self.root.anchor
        if os.name != 'nt' or len(anchor) != 3 or anchor[1:] != ':\\':
            raise OSError('A plain absolute Windows directory is required')
        k = self.kernel = c.WinDLL('kernel32', use_last_error=True)
        self.nt = c.WinDLL('ntdll')
        k.CreateFileW.argtypes = (w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE)
        k.CreateFileW.restype = w.HANDLE
        k.CloseHandle.argtypes = (w.HANDLE,)
        k.GetFileInformationByHandleEx.argtypes = (w.HANDLE, c.c_int, w.LPVOID, w.DWORD)
        k.GetFileInformationByHandleEx.restype = w.BOOL
        k.GetFileSizeEx.argtypes = (w.HANDLE, c.POINTER(c.c_int64))
        k.GetFileSizeEx.restype = w.BOOL
        self.nt.NtCreateFile.argtypes = (c.POINTER(w.HANDLE), w.ULONG, c.POINTER(ObjectAttributes),
                                         c.POINTER(IoStatus), w.LPVOID, w.ULONG, w.ULONG, w.ULONG,
                                         w.ULONG, w.LPVOID, w.ULONG)
        self.nt.NtCreateFile.restype = w.LONG
        self.nt.RtlNtStatusToDosError.argtypes = (w.LONG,)
        self.nt.RtlNtStatusToDosError.restype = w.ULONG
        handle = k.CreateFileW(anchor, 0x80 | 0x20, 3, None, 3, 0x02200000, None)
        if handle in (None, c.c_void_p(-1).value):
            raise c.WinError(c.get_last_error())
        self.handle = handle
        if len(self.root.parts) > 1:
            try:
                self.handle = self._open('/'.join(self.root.parts[1:]), 0x1000a0, 0x21, 3).value
            except BaseException:
                self.handle = None
                raise
            finally:
                k.CloseHandle(handle)
        return self

    def __exit__(self, *_):
        self.kernel.CloseHandle(self.handle)
        self.handle = None

    def _identity(self, handle):
        info = FileIdInfo()
        if not self.kernel.GetFileInformationByHandleEx(handle, 18, c.byref(info), c.sizeof(info)):
            raise c.WinError(c.get_last_error())
        file_id = bytes(info.file_id)
        if not any(file_id):
            raise ValueError('File identity is unavailable')
        return info.volume_serial, file_id

    def identity(self, stream):
        import msvcrt

        return self._identity(msvcrt.get_osfhandle(stream.fileno()))

    def open(self, relative, *, expected_identity=None):
        import msvcrt

        handle = self._open(relative, 0x80100000, 0x60, 1)
        try:
            if expected_identity is not None and self._identity(handle) != expected_identity:
                raise ValueError(f'File identity changed: {relative}')
            fd = msvcrt.open_osfhandle(handle.value, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            self.kernel.CloseHandle(handle)
            raise
        try:
            return os.fdopen(fd, 'rb')
        except BaseException:
            os.close(fd)
            raise

    def pin(self, relative, expected_size):
        """Research only: validate size and presence of an ID on a retained native handle."""
        handle = self._open(relative, 0x80100000, 0x60, 1)
        try:
            size = c.c_int64()
            if not self.kernel.GetFileSizeEx(handle, c.byref(size)):
                raise c.WinError(c.get_last_error())
            self._identity(handle)
            if size.value != expected_size:
                raise ValueError(f'Unexpected file identity or size: {relative}')
            return handle
        except BaseException:
            self.unpin(handle)
            raise

    def unpin(self, handle):
        self.kernel.CloseHandle(handle)

    def _open(self, relative, access, options, sharing):
        parts = relative.split('/')
        if any(not p or _SPECIAL.search(p) for p in parts):
            raise OSError('Unsupported relative Windows path')
        path = '\\'.join(parts)
        length = len(path.encode('utf-16-le'))
        if length > 65532:
            raise OSError('Relative path too long')
        name = UnicodeString(length, length + 2, path)
        attributes = ObjectAttributes(c.sizeof(ObjectAttributes), self.handle, c.pointer(name),
                                      0x1040, None, None)  # OBJ_DONT_REPARSE | OBJ_CASE_INSENSITIVE
        handle = w.HANDLE()
        status = self.nt.NtCreateFile(c.byref(handle), access, c.byref(attributes), c.byref(IoStatus()),
                                      None, 0, sharing, 1, options, None, 0)
        if status < 0:
            raise c.WinError(self.nt.RtlNtStatusToDosError(status))
        return handle

def read_inventory(tree, *, directory_ids=None):
    """Read-only exact-name/size/ID inventory of a PlainTree.

    Not an atomic snapshot: it records what each directory yielded while walked.
    """
    import ctypes as _c
    import struct
    from collections import deque

    k = tree.kernel
    root_identity = tree._identity(tree.handle)
    result = {}
    pending = deque([('', root_identity)])
    buf = _c.create_string_buffer(65536)
    while pending:
        prefix, expected_identity = pending.popleft()
        if prefix:
            handle = tree._open(prefix, 0x1000A1, 0x21, 7)
        else:
            handle = k.CreateFileW(str(tree.root), 0x81, 7, None, 3, 0x02200000, None)
            if handle in (None, _c.c_void_p(-1).value):
                raise _c.WinError(_c.get_last_error())
        try:
            if tree._identity(handle) != expected_identity:
                raise ValueError(f'Directory identity changed: {prefix!r}')
            if directory_ids is not None:
                if prefix in directory_ids:
                    raise ValueError(f'Duplicate directory: {prefix!r}')
                directory_ids[prefix] = expected_identity
            mode = 20  # FileIdExtdDirectoryRestartInfo
            while True:
                if not k.GetFileInformationByHandleEx(handle, mode, buf, len(buf)):
                    error = _c.get_last_error()
                    if error == 18:  # ERROR_NO_MORE_FILES
                        break
                    raise _c.WinError(error)
                mode = 19  # FileIdExtdDirectoryInfo
                pos = 0
                while True:
                    if pos + 88 > len(buf):
                        raise ValueError('Truncated directory record')
                    following, = struct.unpack_from('<I', buf, pos)
                    size, = struct.unpack_from('<q', buf, pos + 40)
                    attributes, length = struct.unpack_from('<II', buf, pos + 56)
                    end = pos + 88 + length
                    if not length or length % 2 or end > len(buf) or (following and (
                            following % 8 or pos + following < end or pos + following >= len(buf))):
                        raise ValueError('Invalid directory record bounds')
                    name = buf[pos + 88:end].decode('utf-16-le')
                    if name not in ('.', '..') and (prefix or name.casefold() not in ('.heylisten-get', '.heylisten-parts')):
                        path = f'{prefix}/{name}' if prefix else name
                        file_id = buf[pos + 72:pos + 88]
                        if not any(file_id) or attributes & 0x400 or '/' in name or '\\' in name:
                            raise ValueError(f'Untrackable directory entry: {path!r}')
                        identity = (root_identity[0], file_id)
                        if attributes & 0x10:
                            pending.append((path, identity))
                        else:
                            if path in result:
                                raise ValueError(f'Duplicate directory entry: {path!r}')
                            result[path] = (size, identity)
                    if not following:
                        break
                    pos += following
        finally:
            k.CloseHandle(handle)
    return result
