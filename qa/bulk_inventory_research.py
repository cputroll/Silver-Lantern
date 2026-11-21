"""Read-only NTFS inventory for the full-SHA research benchmark."""

import ctypes as c
import struct
from collections import deque


def read_inventory(tree, *, directory_ids=None):
    """Return exact names, sizes and IDs; not a consistent namespace snapshot."""
    k = tree.kernel
    root_identity = tree._identity(tree.handle)
    result = {}
    pending = deque([('', root_identity)])
    buf = c.create_string_buffer(65536)
    while pending:
        prefix, expected_identity = pending.popleft()
        if prefix:
            handle = tree._open(prefix, 0x1000A1, 0x21, 7)
        else:
            handle = k.CreateFileW(str(tree.root), 0x81, 7, None, 3, 0x02200000, None)
            if handle in (None, c.c_void_p(-1).value):
                raise c.WinError(c.get_last_error())
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
                    error = c.get_last_error()
                    if error == 18:  # ERROR_NO_MORE_FILES
                        break
                    raise c.WinError(error)
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
