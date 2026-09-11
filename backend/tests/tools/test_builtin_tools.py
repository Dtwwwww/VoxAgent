from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from voxagent.db.migrations import migrate
from voxagent.tools.builtin import build_builtin_registry
from voxagent.tools.registry import FrozenToolRegistryError
from voxagent.tools.schema import PermissionLevel, ToolCall, ToolDefinition


class FakeKnowledgeSource:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def search_knowledge(self, query: str, limit: int) -> list[SimpleNamespace]:
        self.calls.append((query, limit))
        return [
            SimpleNamespace(
                chunk_id=11,
                document_id=3,
                display_name="Agent Notes",
                content="bounded tools",
                page_number=None,
                score=0.82,
                source_path=r"C:\private\notes.txt",
            )
        ]


class FakeLauncher:
    def __init__(self) -> None:
        self.opened: list[str] = []

    def open_app(self, app_id: str) -> None:
        self.opened.append(app_id)


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def open_test_database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    migrate(connection)
    return connection


def build_registry(connection: sqlite3.Connection, *, root: Path | None = None):
    if root is not None:
        connection.execute(
            """
            INSERT INTO authorized_roots(display_name, canonical_path, created_at_utc)
            VALUES ('workspace', ?, '2026-09-10T00:00:00.000Z')
            """,
            (str(root.resolve(strict=True)),),
        )
    return build_builtin_registry(
        connection,
        FakeKnowledgeSource(),
        FakeLauncher(),
        now_utc=lambda: datetime(2026, 9, 10, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_builtin_registry_contains_exactly_six_frozen_tools() -> None:
    connection = open_test_database()
    registry = build_registry(connection)

    payloads = registry.definition_payloads()

    assert {payload["function"]["name"] for payload in payloads} == {
        "knowledge.search",
        "files.search_authorized",
        "reminders.list",
        "reminders.create",
        "reminders.complete",
        "apps.open_allowlisted",
    }
    permissions = {
        name: registry.get(name)[0].permission
        for name in {payload["function"]["name"] for payload in payloads}
    }
    assert permissions == {
        "knowledge.search": PermissionLevel.L0,
        "files.search_authorized": PermissionLevel.L0,
        "reminders.list": PermissionLevel.L0,
        "reminders.create": PermissionLevel.L2,
        "reminders.complete": PermissionLevel.L2,
        "apps.open_allowlisted": PermissionLevel.L1,
    }
    with pytest.raises(FrozenToolRegistryError):
        registry.register(
            ToolDefinition(
                name="knowledge.extra",
                description="extra",
                permission=PermissionLevel.L0,
                arguments_model=EmptyArgs,
                timeout_seconds=1,
                provider="native",
            ),
            registry.get("knowledge.search")[1],
        )


@pytest.mark.asyncio
async def test_knowledge_search_returns_sanitized_fields() -> None:
    connection = open_test_database()
    source = FakeKnowledgeSource()
    registry = build_builtin_registry(
        connection,
        source,
        FakeLauncher(),
        now_utc=lambda: datetime(2026, 9, 10, tzinfo=UTC),
    )
    definition, _ = registry.get("knowledge.search")

    result = await registry.execute(
        ToolCall.from_untrusted(definition, "call-1", {"query": "agent", "limit": 1})
    )

    assert source.calls == [("agent", 1)]
    assert result.status == "succeeded"
    assert result.data == {
        "results": [
            {
                "chunk_id": 11,
                "document_id": 3,
                "display_name": "Agent Notes",
                "content": "bounded tools",
                "page_number": None,
                "score": 0.82,
            }
        ]
    }
    assert "private" not in str(result.data)


@pytest.mark.asyncio
async def test_file_search_returns_relative_matches_and_unknown_root_is_sanitized(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    (root / "nested").mkdir(parents=True)
    (root / "nested" / "Agent-Plan.md").write_text("ignored", encoding="utf-8")
    (root / "other.txt").write_text("ignored", encoding="utf-8")
    connection = open_test_database()
    registry = build_registry(connection, root=root)
    definition, _ = registry.get("files.search_authorized")

    result = await registry.execute(
        ToolCall.from_untrusted(
            definition,
            "call-1",
            {"query": "agent", "root_id": 1, "limit": 20},
        )
    )
    missing = await registry.execute(
        ToolCall.from_untrusted(
            definition,
            "call-2",
            {"query": "agent", "root_id": 999, "limit": 20},
        )
    )

    assert result.status == "succeeded"
    assert result.data == {"paths": ["nested/Agent-Plan.md"]}
    assert missing.status == "failed"
    assert missing.error_code == "authorized_root_not_found"
    assert missing.data == {}
    assert str(tmp_path) not in missing.user_summary


@pytest.mark.asyncio
async def test_reminders_create_list_complete_and_reject_duplicate_completion() -> None:
    connection = open_test_database()
    registry = build_registry(connection)

    create_definition, _ = registry.get("reminders.create")
    list_definition, _ = registry.get("reminders.list")
    complete_definition, _ = registry.get("reminders.complete")

    created = await registry.execute(
        ToolCall.from_untrusted(
            create_definition,
            "call-1",
            {
                "title": "准备 Agent 演示",
                "due_at_utc": "2026-09-11T01:30:00Z",
            },
        )
    )
    listed = await registry.execute(ToolCall.from_untrusted(list_definition, "call-2", {}))
    completed = await registry.execute(
        ToolCall.from_untrusted(
            complete_definition,
            "call-3",
            {"reminder_id": created.data["reminder"]["id"]},
        )
    )
    duplicate = await registry.execute(
        ToolCall.from_untrusted(
            complete_definition,
            "call-4",
            {"reminder_id": created.data["reminder"]["id"]},
        )
    )

    assert created.status == "succeeded"
    assert created.data["reminder"]["due_at_utc"] == "2026-09-11T01:30:00.000Z"
    assert listed.data["reminders"] == [created.data["reminder"]]
    assert completed.status == "succeeded"
    assert duplicate.status == "failed"
    assert duplicate.error_code == "reminder_not_open"


@pytest.mark.asyncio
async def test_app_launcher_is_allowlisted_and_invalid_ids_fail_validation() -> None:
    connection = open_test_database()
    launcher = FakeLauncher()
    registry = build_builtin_registry(
        connection,
        FakeKnowledgeSource(),
        launcher,
        now_utc=lambda: datetime(2026, 9, 10, tzinfo=UTC),
    )
    definition, _ = registry.get("apps.open_allowlisted")

    notepad = await registry.execute(
        ToolCall.from_untrusted(definition, "call-1", {"app_id": "notepad"})
    )
    calculator = await registry.execute(
        ToolCall.from_untrusted(definition, "call-2", {"app_id": "calculator"})
    )

    assert notepad.status == "succeeded"
    assert calculator.status == "succeeded"
    assert launcher.opened == ["notepad", "calculator"]
    with pytest.raises(ValidationError):
        ToolCall.from_untrusted(definition, "call-3", {"app_id": "cmd"})
