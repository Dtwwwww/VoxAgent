from __future__ import annotations

import ctypes
import sys

import pytest

import voxagent.tools.app_launcher as app_launcher_module
import voxagent.tools.windows_app_activation as activation_module
from voxagent.tools.app_launcher import AppActivationError, WindowsAllowlistedLauncher
from voxagent.tools.windows_app_activation import Win32WindowBackend


class FakeWindowBackend:
    def __init__(
        self,
        *,
        process: bool,
        hwnd: int | None,
        minimized: bool = False,
        focus_result: bool = True,
    ) -> None:
        self.process = process
        self.hwnd = hwnd
        self.minimized = minimized
        self.focus_result = focus_result
        self.calls: list[tuple[str, int]] = []

    def find_window(self, process_name: str) -> int | None:
        return self.hwnd

    def process_exists(self, process_name: str) -> bool:
        return self.process

    def is_minimized(self, hwnd: int) -> bool:
        return self.minimized

    def restore(self, hwnd: int) -> None:
        self.calls.append(("restore", hwnd))

    def focus(self, hwnd: int) -> bool:
        self.calls.append(("focus", hwnd))
        return self.focus_result

    def flash(self, hwnd: int) -> None:
        self.calls.append(("flash", hwnd))


class FakeUser32:
    def __init__(self, enum_windows) -> None:
        self.EnumWindows = enum_windows

    @staticmethod
    def IsWindowVisible(hwnd: int) -> bool:
        return True


def native_backend(user32: FakeUser32) -> Win32WindowBackend:
    backend = object.__new__(Win32WindowBackend)
    backend._user32 = user32
    backend._kernel32 = object()
    return backend


def test_launches_once_when_target_process_is_absent() -> None:
    backend = FakeWindowBackend(process=False, hwnd=None)
    started: list[list[str]] = []
    launcher = WindowsAllowlistedLauncher(backend=backend, start=lambda argv: started.append(argv))

    assert launcher.open_app("notepad").outcome == "launched"
    assert started == [["notepad.exe"]]


def test_restores_and_focuses_existing_window_without_starting() -> None:
    backend = FakeWindowBackend(process=True, hwnd=42, minimized=True, focus_result=True)
    launcher = WindowsAllowlistedLauncher(
        backend=backend, start=lambda argv: pytest.fail(str(argv))
    )

    assert launcher.open_app("calculator").outcome == "focused"
    assert backend.calls == [("restore", 42), ("focus", 42)]


def test_flashes_when_windows_denies_foreground_focus() -> None:
    backend = FakeWindowBackend(process=True, hwnd=42, focus_result=False)
    launcher = WindowsAllowlistedLauncher(
        backend=backend, start=lambda argv: pytest.fail(str(argv))
    )

    assert launcher.open_app("calculator").outcome == "attention_requested"
    assert backend.calls[-1] == ("flash", 42)


def test_fails_without_starting_when_existing_process_never_exposes_window() -> None:
    backend = FakeWindowBackend(process=True, hwnd=None)
    started: list[list[str]] = []
    now = 0.0

    def monotonic() -> float:
        return now

    def wait(seconds: float) -> None:
        nonlocal now
        now += seconds

    launcher = WindowsAllowlistedLauncher(
        backend=backend,
        start=lambda argv: started.append(argv),
        monotonic=monotonic,
        wait=wait,
    )

    with pytest.raises(AppActivationError, match="app_window_unavailable"):
        launcher.open_app("calculator")

    assert started == []


def test_matching_window_callback_stop_is_success(monkeypatch) -> None:
    def enum_windows(callback, _parameter) -> bool:
        return bool(callback(42, 0))

    backend = native_backend(FakeUser32(enum_windows))
    monkeypatch.setattr(backend, "_window_or_descendant_matches", lambda *_args: True)

    assert backend.find_window("CalculatorApp.exe") == 42


def test_native_window_enumeration_failure_never_launches_duplicate() -> None:
    def enum_windows(_callback, _parameter) -> bool:
        ctypes.set_last_error(5)
        return False

    backend = native_backend(FakeUser32(enum_windows))
    started: list[list[str]] = []
    launcher = WindowsAllowlistedLauncher(backend=backend, start=lambda argv: started.append(argv))

    with pytest.raises(AppActivationError, match="app_activation_failed"):
        launcher.open_app("calculator")

    assert started == []


def test_window_enumeration_callback_exception_fails_safely(monkeypatch) -> None:
    def enum_windows(callback, _parameter) -> bool:
        return bool(callback(42, 0))

    backend = native_backend(FakeUser32(enum_windows))
    monkeypatch.setattr(
        backend,
        "_window_or_descendant_matches",
        lambda _hwnd, _process_name: (_ for _ in ()).throw(RuntimeError("native detail")),
    )
    started: list[list[str]] = []
    launcher = WindowsAllowlistedLauncher(backend=backend, start=lambda argv: started.append(argv))

    with pytest.raises(AppActivationError, match="app_activation_failed"):
        launcher.open_app("calculator")

    assert started == []


def test_launcher_does_not_construct_default_win32_backend_until_action(monkeypatch) -> None:
    constructed = 0

    def construct_backend():
        nonlocal constructed
        constructed += 1
        return FakeWindowBackend(process=False, hwnd=None)

    monkeypatch.setattr(app_launcher_module, "Win32WindowBackend", construct_backend)

    launcher = WindowsAllowlistedLauncher(start=lambda _argv: None)

    assert constructed == 0
    assert launcher.open_app("notepad").outcome == "launched"
    assert constructed == 1


def test_non_windows_adapter_constructs_and_open_app_fails_safely(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        activation_module.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: pytest.fail("WinDLL must not be loaded on non-Windows"),
    )
    started: list[list[str]] = []
    backend = Win32WindowBackend()
    launcher = WindowsAllowlistedLauncher(backend=backend, start=lambda argv: started.append(argv))

    with pytest.raises(AppActivationError, match="app_activation_failed"):
        launcher.open_app("calculator")

    assert started == []
