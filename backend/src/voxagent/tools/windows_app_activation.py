from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Protocol

import psutil


class AppWindowBackend(Protocol):
    def find_window(self, process_name: str) -> int | None: ...

    def process_exists(self, process_name: str) -> bool: ...

    def is_minimized(self, hwnd: int) -> bool: ...

    def restore(self, hwnd: int) -> None: ...

    def focus(self, hwnd: int) -> bool: ...

    def flash(self, hwnd: int) -> None: ...


class _FlashWindowInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("hwnd", wintypes.HWND),
        ("dwFlags", wintypes.DWORD),
        ("uCount", wintypes.UINT),
        ("dwTimeout", wintypes.DWORD),
    ]


class Win32WindowBackend:
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _SW_RESTORE = 9
    _FLASHW_TRAY = 0x00000002
    _FLASHW_TIMERNOFG = 0x0000000C

    def __init__(self) -> None:
        self._user32 = None
        self._kernel32 = None

    def find_window(self, process_name: str) -> int | None:
        self._ensure_native()
        matching_window: int | None = None
        callback_error: Exception | None = None

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit_window(hwnd: int, _: int) -> bool:
            nonlocal callback_error, matching_window
            try:
                if not self._user32.IsWindowVisible(hwnd):
                    return True
                if self._window_or_descendant_matches(hwnd, process_name):
                    matching_window = hwnd
                    return False
                return True
            except Exception as error:
                callback_error = error
                return False

        ctypes.set_last_error(0)
        try:
            enumeration_succeeded = bool(self._user32.EnumWindows(visit_window, 0))
            last_error = ctypes.get_last_error()
        finally:
            ctypes.set_last_error(0)
        if matching_window is not None:
            return matching_window
        if callback_error is not None:
            raise OSError("window enumeration callback failed") from callback_error
        if not enumeration_succeeded:
            raise OSError(last_error, "window enumeration failed")
        return matching_window

    def process_exists(self, process_name: str) -> bool:
        expected_name = process_name.casefold()
        for process in psutil.process_iter(("name", "exe")):
            try:
                name = process.info["name"] or Path(process.info["exe"] or "").name
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
            if name.casefold() == expected_name:
                return True
        return False

    def is_minimized(self, hwnd: int) -> bool:
        self._ensure_native()
        return bool(self._user32.IsIconic(hwnd))

    def restore(self, hwnd: int) -> None:
        self._ensure_native()
        self._user32.ShowWindow(hwnd, self._SW_RESTORE)

    def focus(self, hwnd: int) -> bool:
        self._ensure_native()
        return bool(self._user32.SetForegroundWindow(hwnd))

    def flash(self, hwnd: int) -> None:
        self._ensure_native()
        info = _FlashWindowInfo(
            cbSize=ctypes.sizeof(_FlashWindowInfo),
            hwnd=hwnd,
            dwFlags=self._FLASHW_TRAY | self._FLASHW_TIMERNOFG,
            uCount=3,
            dwTimeout=0,
        )
        self._user32.FlashWindowEx(ctypes.byref(info))

    def _window_or_descendant_matches(self, hwnd: int, process_name: str) -> bool:
        if self._process_matches(self._window_process_id(hwnd), process_name):
            return True

        matched = False
        callback_error: Exception | None = None

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit_child(child: int, _: int) -> bool:
            nonlocal callback_error, matched
            try:
                if self._process_matches(self._window_process_id(child), process_name):
                    matched = True
                    return False
                return True
            except Exception as error:
                callback_error = error
                return False

        ctypes.set_last_error(0)
        try:
            enumeration_succeeded = bool(self._user32.EnumChildWindows(hwnd, visit_child, 0))
            last_error = ctypes.get_last_error()
        finally:
            ctypes.set_last_error(0)
        if callback_error is not None:
            raise OSError("child window enumeration callback failed") from callback_error
        if not matched and not enumeration_succeeded:
            raise OSError(last_error, "child window enumeration failed")
        return matched

    def _window_process_id(self, hwnd: int) -> int:
        process_id = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        return process_id.value

    def _process_matches(self, process_id: int, process_name: str) -> bool:
        if not process_id:
            return False
        process = self._kernel32.OpenProcess(
            self._PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            process_id,
        )
        if not process:
            return False
        try:
            path = ctypes.create_unicode_buffer(32768)
            path_length = wintypes.DWORD(len(path))
            if not self._kernel32.QueryFullProcessImageNameW(
                process,
                0,
                path,
                ctypes.byref(path_length),
            ):
                return False
            return Path(path.value).name.casefold() == process_name.casefold()
        finally:
            self._kernel32.CloseHandle(process)

    def _configure_native_calls(self) -> None:
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self._user32.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
        self._user32.EnumWindows.restype = wintypes.BOOL
        self._user32.EnumChildWindows.argtypes = (
            wintypes.HWND,
            callback_type,
            wintypes.LPARAM,
        )
        self._user32.EnumChildWindows.restype = wintypes.BOOL
        self._user32.GetWindowThreadProcessId.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        )
        self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self._user32.IsWindowVisible.argtypes = (wintypes.HWND,)
        self._user32.IsWindowVisible.restype = wintypes.BOOL
        self._user32.IsIconic.argtypes = (wintypes.HWND,)
        self._user32.IsIconic.restype = wintypes.BOOL
        self._user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        self._user32.ShowWindow.restype = wintypes.BOOL
        self._user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
        self._user32.SetForegroundWindow.restype = wintypes.BOOL
        self._user32.FlashWindowEx.argtypes = (ctypes.POINTER(_FlashWindowInfo),)
        self._user32.FlashWindowEx.restype = wintypes.BOOL
        self._kernel32.OpenProcess.argtypes = (
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        )
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        self._kernel32.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        )
        self._kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    def _ensure_native(self) -> None:
        if self._user32 is not None and self._kernel32 is not None:
            return
        if sys.platform != "win32":
            raise OSError("Windows app activation is unavailable")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_native_calls()
