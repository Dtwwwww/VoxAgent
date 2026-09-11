from __future__ import annotations

import base64
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from voxagent.api.app import create_app
from voxagent.db.migrations import migrate
from voxagent.tools.repository import ToolRepository
from voxagent.tools.schema import PermissionLevel, ToolCall

TOKEN = base64.urlsafe_b64encode(b"a" * 32).decode().rstrip("=")
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeOrchestrator:
    model_id = "test"
    state = SimpleNamespace(session_id=uuid4())
    voice_catalog = SimpleNamespace(public_profiles=lambda: ())


def test_tool_audit_is_authenticated_paginated_and_does_not_expose_arguments() -> None:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    migrate(connection)
    repository = ToolRepository(connection)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    for index in range(3):
        repository.create_request(
            "session-1",
            1,
            ToolCall(
                call_id=f"call-{index}",
                name="file.search",
                arguments={"path": "D:/private"},
            ),
            PermissionLevel.L1,
            "a" * 64,
            now,
        )
    client = TestClient(create_app(lambda: FakeOrchestrator(), TOKEN, tool_audit_reader=repository))

    assert client.get("/v1/tool-audit").status_code == 401
    response = client.get("/v1/tool-audit?limit=2&offset=1", headers=AUTH)
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert all("arguments" not in row and "arguments_sha256" not in row for row in response.json())
    connection.close()
