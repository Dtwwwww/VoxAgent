from __future__ import annotations

import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic, perf_counter, sleep
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from voxagent.tools.schema import ToolCall, ToolResult
from voxagent.tools.windows_app_activation import AppWindowBackend, Win32WindowBackend

AppId = Literal["notepad", "calculator"]


class Launcher(Protocol):
    def open_app(self, app_id: str) -> AppActivationResult: ...


class AppActivationError(RuntimeError):
    _VALID_CODES = {
        "app_launch_failed",
        "app_window_unavailable",
        "app_activation_failed",
    }

    def __init__(self, code: str) -> None:
        if code not in self._VALID_CODES:
            raise ValueError(f"unsupported app activation code: {code}")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AppActivationResult:
    outcome: Literal["launched", "focused", "attention_requested"]


class AppOpenArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_id: AppId


class WindowsAllowlistedLauncher:
    _SPECS = {
        "notepad": ("notepad.exe", "Notepad.exe"),
        "calculator": ("calc.exe", "CalculatorApp.exe"),
    }

    def __init__(
        self,
        *,
        backend: AppWindowBackend | None = None,
        start: Callable[[list[str]], object] | None = None,
        monotonic: Callable[[], float] = monotonic,
        wait: Callable[[float], None] = sleep,
    ) -> None:
        self._backend = backend or Win32WindowBackend()
        self._start = start or self._start_process
        self._monotonic = monotonic
        self._wait = wait

    def open_app(self, app_id: str) -> AppActivationResult:
        command, process_name = self._SPECS[app_id]
        try:
            hwnd = self._backend.find_window(process_name)
            if hwnd is not None:
                return self._activate(hwnd)
            if self._backend.process_exists(process_name):
                hwnd = self._wait_for_window(process_name)
                if hwnd is None:
                    raise AppActivationError("app_window_unavailable")
                return self._activate(hwnd)
        except AppActivationError:
            raise
        except OSError as error:
            raise AppActivationError("app_activation_failed") from error

        try:
            self._start([command])
        except OSError as error:
            raise AppActivationError("app_launch_failed") from error
        return AppActivationResult("launched")

    def _activate(self, hwnd: int) -> AppActivationResult:
        if self._backend.is_minimized(hwnd):
            self._backend.restore(hwnd)
        if self._backend.focus(hwnd):
            return AppActivationResult("focused")
        self._backend.flash(hwnd)
        return AppActivationResult("attention_requested")

    def _wait_for_window(self, process_name: str) -> int | None:
        deadline = self._monotonic() + 1.0
        while True:
            hwnd = self._backend.find_window(process_name)
            if hwnd is not None:
                return hwnd
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return None
            self._wait(min(0.05, remaining))

    @staticmethod
    def _start_process(argv: list[str]) -> object:
        return subprocess.Popen(argv, close_fds=True)


_APP_LABELS = {"notepad": "记事本", "calculator": "计算器"}
_OUTCOME_COPY = {
    "launched": "已启动{label}。",
    "focused": "已切换到{label}。",
    "attention_requested": "{label}已在任务栏提示，请点击切换。",
}


def build_app_launcher_executor(
    launcher: Launcher,
) -> Callable[[ToolCall], Awaitable[ToolResult]]:
    async def execute(call: ToolCall) -> ToolResult:
        started = perf_counter()
        app_id = call.arguments["app_id"]
        try:
            result = launcher.open_app(app_id)
        except AppActivationError as error:
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.name,
                status="failed",
                user_summary="应用启动失败。",
                error_code=error.code,
                duration_ms=_duration_ms(started),
            )
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            status="succeeded",
            data={"app_id": app_id, "outcome": result.outcome},
            user_summary=_OUTCOME_COPY[result.outcome].format(label=_APP_LABELS[app_id]),
            duration_ms=_duration_ms(started),
        )

    return execute


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
