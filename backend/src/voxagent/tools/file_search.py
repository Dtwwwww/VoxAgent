from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import perf_counter

from pydantic import BaseModel, ConfigDict, Field

from voxagent.tools.path_policy import PathAuthorizationError, PathPolicy
from voxagent.tools.schema import ToolCall, ToolResult

_MAX_DEPTH = 5
_MAX_VISITED = 5000


class FileSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=120)
    root_id: int = Field(gt=0)
    limit: int = Field(default=20, ge=1, le=50)


def build_file_search_executor(
    connection: sqlite3.Connection,
) -> Callable[[ToolCall], Awaitable[ToolResult]]:
    async def execute(call: ToolCall) -> ToolResult:
        started = perf_counter()
        root = _authorized_root(connection, call.arguments["root_id"])
        if root is None:
            return _failure(call, "authorized_root_not_found", started)

        paths = _search(root, call.arguments["query"], call.arguments["limit"])
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            status="succeeded",
            data={"paths": paths},
            user_summary="授权目录搜索完成。",
            duration_ms=_duration_ms(started),
        )

    return execute


def _authorized_root(connection: sqlite3.Connection, root_id: int) -> Path | None:
    row = connection.execute(
        "SELECT canonical_path FROM authorized_roots WHERE id = ?",
        (root_id,),
    ).fetchone()
    if row is None:
        return None
    root = Path(row["canonical_path"])
    try:
        return PathPolicy.resolve_authorized(str(root), (root,))
    except PathAuthorizationError:
        return None


def _search(root: Path, query: str, limit: int) -> list[str]:
    lowered_query = query.casefold()
    matches: list[str] = []
    visited = 0
    stack: list[tuple[Path, int]] = [(root, 0)]

    while stack and len(matches) < limit and visited < _MAX_VISITED:
        current, depth = stack.pop()
        try:
            children = sorted(
                current.iterdir(),
                key=lambda path: path.name.casefold(),
                reverse=True,
            )
        except OSError:
            continue

        for child in children:
            if len(matches) >= limit or visited >= _MAX_VISITED:
                break
            visited += 1
            if _is_link_or_reparse_point(child):
                continue
            try:
                resolved = PathPolicy.resolve_authorized(str(child), (root,))
            except PathAuthorizationError:
                continue
            if child.name.casefold().find(lowered_query) >= 0 and resolved.is_file():
                matches.append(resolved.relative_to(root).as_posix())
            if depth + 1 < _MAX_DEPTH and resolved.is_dir():
                stack.append((resolved, depth + 1))

    return sorted(matches, key=str.casefold)[:limit]


def _is_link_or_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = os.stat(path, follow_symlinks=False).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _failure(call: ToolCall, error_code: str, started: float) -> ToolResult:
    return ToolResult(
        call_id=call.call_id,
        tool_name=call.name,
        status="failed",
        data={},
        user_summary="授权目录不可用。",
        error_code=error_code,
        duration_ms=_duration_ms(started),
    )


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
