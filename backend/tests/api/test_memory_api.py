from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voxagent.api.app import create_app
from voxagent.api.memory import LocalMemoryService
from voxagent.api.persona import LocalPersonaService
from voxagent.db.connection import open_database
from voxagent.db.migrations import migrate

SESSION_TOKEN = base64.urlsafe_b64encode(b"m" * 32).decode("ascii").rstrip("=")
AUTH = {"Authorization": f"Bearer {SESSION_TOKEN}"}


class FakeOrchestrator:
    model_id = "qwen-local"
    state = SimpleNamespace(session_id=uuid4())
    voice_catalog = SimpleNamespace(public_profiles=lambda: ())

    async def stop(self) -> None:
        return None


class FakeEmbedder:
    def encode(self, texts: tuple[str, ...]) -> np.ndarray:
        vectors = np.zeros((len(texts), 512), dtype=np.float32)
        vectors[:, 0] = 1
        return vectors


@pytest.fixture
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, Path]:
    monkeypatch.setenv("VOXAGENT_DATA_ROOT", str(tmp_path))
    database_path = tmp_path / "data" / "voxagent.db"
    connection = open_database(database_path)
    migrate(connection)
    connection.close()
    app = create_app(
        lambda: FakeOrchestrator(),
        SESSION_TOKEN,
        memory_service=LocalMemoryService(database_path, FakeEmbedder()),
        persona_service=LocalPersonaService(database_path),
    )
    return TestClient(app), database_path


def test_memory_and_persona_routes_require_bearer_authentication(
    client: tuple[TestClient, Path],
) -> None:
    web, _ = client

    assert web.get("/v1/memories").status_code == 401
    assert web.get("/v1/persona").status_code == 401
    assert web.get(
        "/v1/memories", headers={"Authorization": "Bearer wrong"}
    ).status_code == 401


def test_memory_crud_reembeds_updates_and_preserves_source_turn(
    client: tuple[TestClient, Path],
) -> None:
    web, _ = client
    created = web.post(
        "/v1/memories",
        headers=AUTH,
        json={
            "kind": "preference",
            "content": "我喜欢无糖咖啡",
            "importance": 0.8,
            "source_turn_id": 12,
            "confirmed": True,
        },
    )

    assert created.status_code == 201
    memory = created.json()
    assert memory["source_turn_id"] == 12
    assert "embedding" not in memory
    listed = web.get("/v1/memories?limit=20&offset=0", headers=AUTH)
    assert listed.json() == [memory]

    updated = web.patch(
        f"/v1/memories/{memory['id']}",
        headers=AUTH,
        json={
            "content": "我喜欢无糖乌龙茶",
            "importance": 0.9,
            "expected_updated_at_utc": memory["updated_at_utc"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["content"] == "我喜欢无糖乌龙茶"
    assert web.delete(f"/v1/memories/{memory['id']}", headers=AUTH).status_code == 204
    assert web.get("/v1/memories", headers=AUTH).json() == []


def test_memory_policy_cannot_be_bypassed_by_confirming_a_secret(
    client: tuple[TestClient, Path],
) -> None:
    web, _ = client

    response = web.post(
        "/v1/memories",
        headers=AUTH,
        json={
            "kind": "profile",
            "content": "请记住我的密码是 abc123456",
            "importance": 1,
            "confirmed": True,
        },
    )

    assert response.status_code == 422
    assert web.get("/v1/memories", headers=AUTH).json() == []


def test_memory_update_rejects_a_stale_optimistic_revision(
    client: tuple[TestClient, Path],
) -> None:
    web, _ = client
    memory = web.post(
        "/v1/memories",
        headers=AUTH,
        json={
            "kind": "habit",
            "content": "我每周三跑步",
            "importance": 0.8,
            "confirmed": True,
        },
    ).json()

    response = web.patch(
        f"/v1/memories/{memory['id']}",
        headers=AUTH,
        json={
            "content": "我每周四跑步",
            "importance": 0.8,
            "expected_updated_at_utc": "2026-01-01T00:00:00.000Z",
        },
    )

    assert response.status_code == 409


def test_persona_defaults_update_and_optimistic_conflict(
    client: tuple[TestClient, Path],
) -> None:
    web, _ = client
    initial = web.get("/v1/persona", headers=AUTH)

    assert initial.status_code == 200
    assert initial.json()["revision"] == 0
    assert initial.json()["config"]["name"] == "声灵"
    edited_config = {**initial.json()["config"], "name": "小灵", "style": "温柔、简洁"}
    updated = web.put(
        "/v1/persona",
        headers=AUTH,
        json={"expected_revision": 0, "config": edited_config},
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 1
    assert updated.json()["config"]["name"] == "小灵"
    stale = web.put(
        "/v1/persona",
        headers=AUTH,
        json={"expected_revision": 0, "config": edited_config},
    )
    assert stale.status_code == 409
