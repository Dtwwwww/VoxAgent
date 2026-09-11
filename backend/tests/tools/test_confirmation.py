from __future__ import annotations

import inspect
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from voxagent.db.connection import open_database
from voxagent.db.migrations import migrate
from voxagent.tools.confirmation import (
    ConfirmationError,
    ConfirmationService,
    arguments_sha256,
    canonical_arguments,
)
from voxagent.tools.policy import PolicyContext
from voxagent.tools.registry import ToolRegistry
from voxagent.tools.repository import ToolRepository
from voxagent.tools.schema import PermissionLevel, ToolCall, ToolDefinition, ToolResult

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    limit: int


@pytest.fixture
def connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("VOXAGENT_DATA_ROOT", str(tmp_path / "runtime"))
    db = open_database(tmp_path / "runtime" / "data" / "voxagent.db")
    migrate(db)
    yield db
    db.close()


def definition(
    *,
    name: str = "knowledge.search",
    permission: PermissionLevel = PermissionLevel.L1,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="Search safely",
        permission=permission,
        arguments_model=SearchArgs,
        timeout_seconds=1.0,
        provider="native",
    )


def registry_with_counter(counter: dict[str, int]) -> ToolRegistry:
    async def executor(tool_call: ToolCall) -> ToolResult:
        counter["count"] += 1
        return ToolResult(
            call_id=tool_call.call_id,
            tool_name=tool_call.name,
            status="succeeded",
            user_summary="done",
            duration_ms=0,
        )

    registry = ToolRegistry()
    registry.register(definition(), executor)
    registry.freeze()
    return registry


def context(*, cancelled: bool = False) -> PolicyContext:
    return PolicyContext(
        session_id="session-1",
        turn_id=7,
        authorized_roots=(Path.cwd(),),
        tool_call_count=0,
        node_visit_count=0,
        cancelled=cancelled,
    )


def call(arguments: dict[str, object] | None = None) -> ToolCall:
    return ToolCall(
        call_id="call-1",
        name="knowledge.search",
        arguments=arguments or {"query": "声灵", "limit": 3},
    )


def service(connection: sqlite3.Connection, counter: dict[str, int]) -> ConfirmationService:
    return ConfirmationService(ToolRepository(connection), registry_with_counter(counter))


def assert_confirmation_error(expected_code: str, func, *args: object) -> None:
    with pytest.raises(ConfirmationError) as error:
        func(*args)
    assert error.value.code == expected_code


def test_canonical_arguments_are_stable_unicode_preserving_and_reject_nan() -> None:
    first = {"b": 1, "a": "声灵"}
    second = {"a": "声灵", "b": 1}

    assert canonical_arguments(first) == canonical_arguments(second)
    assert "声灵".encode() in canonical_arguments(first)
    assert arguments_sha256(first) == arguments_sha256(second)
    with pytest.raises(ValueError):
        canonical_arguments({"bad": float("nan")})


def test_request_persists_normalized_call_and_trusted_permission(
    connection: sqlite3.Connection,
) -> None:
    counter = {"count": 0}
    repository = ToolRepository(connection)
    ticket = ConfirmationService(repository, registry_with_counter(counter)).request(
        ToolCall(
            call_id="call-1",
            name="knowledge.search",
            arguments={"limit": 3, "query": "声灵"},
        ),
        context(),
        NOW,
    )

    stored_ticket, record = repository.get_confirmation_request(ticket.confirmation_id)

    assert record.arguments == {"limit": 3, "query": "声灵"}
    assert record.permission == PermissionLevel.L1
    assert record.status == "awaiting_confirmation"
    assert stored_ticket.arguments_sha256 == arguments_sha256(record.arguments)
    assert counter["count"] == 0


def test_approval_returns_original_call_exactly_once(connection: sqlite3.Connection) -> None:
    counter = {"count": 0}
    confirmation_service = service(connection, counter)
    ticket = confirmation_service.request(call(), context(), NOW)

    approved = confirmation_service.approve(ticket.confirmation_id, "session-1", 7, NOW)

    assert approved == call()
    assert_confirmation_error(
        "confirmation_unavailable",
        confirmation_service.approve,
        ticket.confirmation_id,
        "session-1",
        7,
        NOW,
    )
    assert counter["count"] == 0


@pytest.mark.parametrize(
    ("session_id", "turn_id", "now_utc", "code"),
    [
        ("other-session", 7, NOW, "confirmation_unavailable"),
        ("session-1", 8, NOW, "confirmation_unavailable"),
        ("session-1", 7, NOW + timedelta(seconds=121), "confirmation_unavailable"),
    ],
)
def test_approval_rejects_invalid_ticket_state_without_returning_call(
    connection: sqlite3.Connection,
    session_id: str,
    turn_id: int,
    now_utc: datetime,
    code: str,
) -> None:
    counter = {"count": 0}
    confirmation_service = service(connection, counter)
    ticket = confirmation_service.request(call(), context(), NOW)

    assert_confirmation_error(
        code,
        confirmation_service.approve,
        ticket.confirmation_id,
        session_id,
        turn_id,
        now_utc,
    )
    assert counter["count"] == 0


@pytest.mark.parametrize(
    ("statement", "parameters"),
    [
        (
            "UPDATE tool_requests SET arguments_json = ? WHERE id = 1",
            ('{"limit":1,"query":"hijack"}',),
        ),
        ("UPDATE tool_requests SET arguments_sha256 = ? WHERE id = 1", ("b" * 64,)),
        ("UPDATE tool_confirmations SET arguments_sha256 = ?", ("b" * 64,)),
        ("UPDATE tool_requests SET tool_name = ? WHERE id = 1", ("knowledge.missing",)),
    ],
)
def test_approval_rejects_tampering_and_unknown_tools_without_returning_call(
    connection: sqlite3.Connection,
    statement: str,
    parameters: tuple[object, ...],
) -> None:
    counter = {"count": 0}
    confirmation_service = service(connection, counter)
    ticket = confirmation_service.request(call(), context(), NOW)
    connection.execute(statement, parameters)

    assert_confirmation_error(
        "confirmation_tampered",
        confirmation_service.approve,
        ticket.confirmation_id,
        "session-1",
        7,
        NOW,
    )
    assert counter["count"] == 0


def test_approve_signature_accepts_only_ticket_identity_not_substitute_arguments() -> None:
    signature = inspect.signature(ConfirmationService.approve)

    assert list(signature.parameters) == [
        "self",
        "confirmation_id",
        "session_id",
        "turn_id",
        "now_utc",
    ]


def test_approval_uses_persisted_arguments_not_mutated_caller_object(
    connection: sqlite3.Connection,
) -> None:
    counter = {"count": 0}
    confirmation_service = service(connection, counter)
    original_call = call()
    ticket = confirmation_service.request(original_call, context(), NOW)
    original_call.arguments["query"] = "hijack"

    approved = confirmation_service.approve(ticket.confirmation_id, "session-1", 7, NOW)

    assert approved.arguments == {"limit": 3, "query": "声灵"}
    assert counter["count"] == 0


def test_request_rejects_unknown_tool_and_cancelled_context_without_persisting(
    connection: sqlite3.Connection,
) -> None:
    counter = {"count": 0}
    confirmation_service = service(connection, counter)

    assert_confirmation_error(
        "unknown_tool",
        confirmation_service.request,
        ToolCall(call_id="call-1", name="knowledge.missing", arguments={}),
        context(),
        NOW,
    )
    assert_confirmation_error(
        "turn_cancelled",
        confirmation_service.request,
        call(),
        context(cancelled=True),
        NOW,
    )
    assert connection.execute("SELECT COUNT(*) FROM tool_requests").fetchone()[0] == 0
    assert counter["count"] == 0
