from __future__ import annotations

import pytest

from voxagent.tools.app_launcher import AppActivationError, WindowsAllowlistedLauncher


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
