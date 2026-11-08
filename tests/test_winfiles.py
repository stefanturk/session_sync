"""Reading a file another program holds open with delete access (as Live does)."""

import os

import pytest

from sessionsync.core import hashing
from sessionsync.core.winfiles import open_for_reading

windows = pytest.mark.skipif(os.name != "nt", reason="Windows file sharing")


def test_reading_works_everywhere(tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(b"audio")
    with open_for_reading(f) as fh:
        assert fh.read() == b"audio"
    with pytest.raises(FileNotFoundError):
        open_for_reading(tmp_path / "missing.wav")


@windows
def test_a_file_held_with_delete_access_is_still_read(tmp_path):
    import ctypes
    from ctypes import wintypes

    f = tmp_path / "Take 3.wav"
    f.write_bytes(b"take 3")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    GENERIC_READ, DELETE = 0x80000000, 0x00010000
    held = kernel32.CreateFileW(str(f), GENERIC_READ | DELETE, 0x1 | 0x4, None, 3, 0, None)
    assert held not in (None, ctypes.c_void_p(-1).value)
    try:
        with pytest.raises(PermissionError):
            open(f, "rb").close()  # what Python alone gets
        assert hashing.hash_file(f) == hashing.hashlib.sha256(b"take 3").hexdigest()
    finally:
        kernel32.CloseHandle(held)
