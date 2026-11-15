"""Installed/portable layout and a session-local lifetime guard; no private reads."""
import ctypes
from ctypes import wintypes as W
import json
import os
from pathlib import Path

APP_MUTEX = r'Local\AgyConsole.Foreground.v1'
SETUP_MUTEX = r'Local\AgyConsole.Setup.v1'


def data_directory(root, override=None):
    if override:
        return Path(override).expanduser().resolve()
    marker = Path(root) / 'installed-mode.json'
    if not marker.exists():
        return Path(root) / 'data'
    if marker.is_symlink() or json.loads(marker.read_text(encoding='utf-8')) != {'schema': 1, 'layout': 'per-user'}:
        raise ValueError('Invalid installation layout; no data was moved.')
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        raise ValueError('LOCALAPPDATA is missing; no data was moved.')
    return Path(base) / 'AgyConsole' / 'data'


class ForegroundGuard:
    """Keep the mutex until supervisor termination, including forced console exit."""
    def __enter__(self):
        if os.name != 'nt':
            raise RuntimeError('Windows is required')
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, W.BOOL, W.LPCWSTR]
        self.kernel.CreateMutexW.restype = W.HANDLE
        self.kernel.OpenMutexW.argtypes = [W.DWORD, W.BOOL, W.LPCWSTR]
        self.kernel.OpenMutexW.restype = W.HANDLE
        self.kernel.CloseHandle.argtypes = [W.HANDLE]
        self.handle = None
        if self.setup_running():
            raise RuntimeError('Installation is in progress. Finish or cancel it before starting Agy Console.')
        self.handle = self.kernel.CreateMutexW(None, False, APP_MUTEX)
        error = ctypes.get_last_error()
        if not self.handle:
            raise ctypes.WinError(error)
        if error == 183:  # ERROR_ALREADY_EXISTS, never kill the owner
            self.close()
            raise RuntimeError('Agy Console is already running. Use its existing foreground window.')
        if self.setup_running():
            self.close()
            raise RuntimeError('Installation started concurrently. Finish it before starting Agy Console.')
        return self

    def setup_running(self):
        handle = self.kernel.OpenMutexW(0x00100000, False, SETUP_MUTEX)
        if handle:
            self.kernel.CloseHandle(handle)
            return True
        error = ctypes.get_last_error()
        if error not in (0, 2):  # Fail closed if an existing guard is inaccessible.
            raise ctypes.WinError(error)
        return False

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __exit__(self, *_):
        self.close()
