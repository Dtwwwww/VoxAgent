from __future__ import annotations

import subprocess
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from voxagent.tools.schema import ToolCall, ToolResult

AppId = Literal["notepad", "calculator"]


class Launcher(Protocol):
    def open_app(self, app_id: str) -> None: ...


class AppOpenArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_id: AppId


class WindowsAllowlistedLauncher:
    _COMMANDS = {
        "notepad": "notepad.exe",
        "calculator": "calc.exe",
    }

    def open_app(self, app_id: str) -> None:
        subprocess.Popen([self._COMMANDS[app_id]], close_fds=True)


def build_app_launcher_executor(
    launcher: Launcher,
) -> Callable[[ToolCall], Awaitable[ToolResult]]:
    async def execute(call: ToolCall) -> ToolResult:
        started = perf_counter()
        launcher.open_app(call.arguments["app_id"])
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            status="succeeded",
            data={"app_id": call.arguments["app_id"]},
            user_summary="应用已打开。",
            duration_ms=_duration_ms(started),
        )

    return execute


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
