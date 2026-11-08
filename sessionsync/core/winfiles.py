"""Reading files another program has open, on Windows.

Python's open() asks Windows to share the file for reading and writing, but not
for delete. A program that opened the file with delete access (Live does, for
files it may rename or replace on save) then makes Windows refuse us:
"[Errno 13] Permission denied". Asking for every kind of sharing reads anything
the holder lets anyone read. Elsewhere this is plain open().
"""

from __future__ import annotations

import os
from pathlib import Path

GENERIC_READ = 0x80000000
FILE_SHARE_ALL = 0x1 | 0x2 | 0x4  # read, write, delete
OPEN_EXISTING = 3
FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
#: sharing violation, lock violation, access denied
_IN_USE = {32, 33, 5}

_create_file = None


def open_for_reading(path: Path):
    """A binary file object for reading `path`, however another program holds it."""
    if os.name != "nt":
        return Path(path).open("rb")
    import ctypes
    import msvcrt

    handle = _create(_long(path))
    if handle is None:
        code = ctypes.get_last_error()
        if code in _IN_USE:
            raise PermissionError(13, "Permission denied", str(path))
        if code in (2, 3):
            raise FileNotFoundError(2, "No such file or directory", str(path))
        raise OSError(code, ctypes.FormatError(code), str(path))
    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    return os.fdopen(fd, "rb")


def _long(path: Path) -> str:
    """Windows paths over 260 characters need the \\\\?\\ prefix for CreateFileW."""
    text = os.path.abspath(str(path))
    if len(text) >= 240 and not text.startswith("\\\\?\\"):
        text = "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text
    return text


def _create(path: str):
    global _create_file
    import ctypes
    from ctypes import wintypes

    if _create_file is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        fn = kernel32.CreateFileW
        fn.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        fn.restype = wintypes.HANDLE
        _create_file = fn
    handle = _create_file(path, GENERIC_READ, FILE_SHARE_ALL, None, OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, None)
    invalid = ctypes.c_void_p(-1).value
    if handle is None or handle == invalid or handle == 0:
        return None
    return handle
