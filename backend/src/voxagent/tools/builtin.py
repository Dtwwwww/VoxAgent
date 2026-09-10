from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from voxagent.tools.app_launcher import (
    AppOpenArgs,
    Launcher,
    build_app_launcher_executor,
)
from voxagent.tools.file_search import FileSearchArgs, build_file_search_executor
from voxagent.tools.knowledge_search import (
    KnowledgeSearchArgs,
    KnowledgeSource,
    build_knowledge_search_executor,
)
from voxagent.tools.registry import ToolRegistry
from voxagent.tools.reminders import (
    ReminderCompleteArgs,
    ReminderCreateArgs,
    ReminderListArgs,
    build_reminder_complete_executor,
    build_reminder_create_executor,
    build_reminder_list_executor,
)
from voxagent.tools.schema import PermissionLevel, ToolDefinition

NowProvider = Callable[[], datetime]


def build_builtin_registry(
    connection: sqlite3.Connection,
    knowledge_source: KnowledgeSource,
    launcher: Launcher,
    *,
    now_utc: NowProvider = lambda: datetime.now(UTC),
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="knowledge.search",
            description="Search imported local knowledge chunks.",
            permission=PermissionLevel.L0,
            arguments_model=KnowledgeSearchArgs,
            timeout_seconds=5,
            provider="native",
        ),
        build_knowledge_search_executor(knowledge_source),
    )
    registry.register(
        ToolDefinition(
            name="files.search_authorized",
            description="Search file names inside one authorized directory.",
            permission=PermissionLevel.L0,
            arguments_model=FileSearchArgs,
            timeout_seconds=5,
            provider="native",
        ),
        build_file_search_executor(connection),
    )
    registry.register(
        ToolDefinition(
            name="reminders.list",
            description="List open reminders.",
            permission=PermissionLevel.L0,
            arguments_model=ReminderListArgs,
            timeout_seconds=3,
            provider="native",
        ),
        build_reminder_list_executor(connection),
    )
    registry.register(
        ToolDefinition(
            name="reminders.create",
            description="Create a local reminder.",
            permission=PermissionLevel.L2,
            arguments_model=ReminderCreateArgs,
            timeout_seconds=3,
            provider="native",
        ),
        build_reminder_create_executor(connection, now_utc),
    )
    registry.register(
        ToolDefinition(
            name="reminders.complete",
            description="Complete an open reminder.",
            permission=PermissionLevel.L2,
            arguments_model=ReminderCompleteArgs,
            timeout_seconds=3,
            provider="native",
        ),
        build_reminder_complete_executor(connection, now_utc),
    )
    registry.register(
        ToolDefinition(
            name="apps.open_allowlisted",
            description="Open one allowlisted local Windows app.",
            permission=PermissionLevel.L1,
            arguments_model=AppOpenArgs,
            timeout_seconds=3,
            provider="native",
        ),
        build_app_launcher_executor(launcher),
    )
    registry.freeze()
    return registry
