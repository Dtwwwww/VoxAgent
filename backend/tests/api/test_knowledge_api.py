from __future__ import annotations

import asyncio
import base64
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voxagent.api.app import create_app
from voxagent.api.knowledge import LocalKnowledgeService
from voxagent.db.connection import open_database
from voxagent.db.migrations import migrate
from voxagent.knowledge.ingest import ImportCancelled

SESSION_TOKEN = base64.urlsafe_b64encode(b"k" * 32).decode("ascii").rstrip("=")
AUTH = {"Authorization": f"Bearer {SESSION_TOKEN}"}


class FakeOrchestrator:
    model_id = "qwen-local"
    state = SimpleNamespace(session_id=uuid4())
    voice_catalog = SimpleNamespace(public_profiles=lambda: ())

    async def stop(self) -> None:
        return None


class FakeKnowledgeService:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, bytes]] = []
        self.deleted: list[int] = []
        self.cancelled = False

    async def list_documents(self) -> tuple[dict[str, object], ...]:
        return (
            {
                "id": 7,
                "display_name": "产品说明.md",
                "sha256": "a" * 64,
                "mime_type": "text/markdown",
                "imported_at_utc": "2026-09-04T08:00:00.000Z",
                "chunk_count": 3,
            },
        )

    async def import_upload(self, filename: str, content: bytes) -> dict[str, object]:
        self.uploads.append((filename, content))
        return {
            "document_id": 8,
            "display_name": filename,
            "created": True,
            "chunk_count": 2,
            "sha256": "b" * 64,
        }

    async def delete_document(self, document_id: int) -> bool:
        self.deleted.append(document_id)
        return document_id == 7

    async def cancel_import(self) -> bool:
        self.cancelled = True
        return True

    async def list_chunks(
        self, document_id: int, limit: int, offset: int
    ) -> tuple[dict[str, object], ...]:
        if document_id != 7:
            return ()
        return (
            {
                "id": 11,
                "ordinal": 0,
                "page_number": 2,
                "content": "这是可核对的来源片段",
            },
        )[offset : offset + limit]


def _client(service: FakeKnowledgeService) -> TestClient:
    return TestClient(
        create_app(lambda: FakeOrchestrator(), SESSION_TOKEN, knowledge_service=service)
    )


def test_knowledge_routes_require_the_session_bearer_token():
    client = _client(FakeKnowledgeService())

    assert client.get("/v1/knowledge").status_code == 401
    assert client.get(
        "/v1/knowledge", headers={"Authorization": "Bearer wrong"}
    ).status_code == 401


def test_knowledge_routes_allow_only_the_local_web_frontend_origin():
    client = _client(FakeKnowledgeService())

    local = client.options(
        "/v1/knowledge",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "PATCH",
        },
    )
    remote = client.options(
        "/v1/knowledge",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert local.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert "PATCH" in local.headers["access-control-allow-methods"]
    assert "access-control-allow-origin" not in remote.headers


def test_lists_public_document_metadata_without_source_paths():
    client = _client(FakeKnowledgeService())

    response = client.get("/v1/knowledge", headers=AUTH)

    assert response.status_code == 200
    assert response.json()[0]["display_name"] == "产品说明.md"
    assert "source_path" not in response.text


def test_imports_raw_local_file_bytes_and_preserves_the_display_name():
    service = FakeKnowledgeService()
    client = _client(service)

    response = client.post(
        "/v1/knowledge",
        params={"filename": "我的资料.txt"},
        headers={**AUTH, "Content-Type": "application/octet-stream"},
        content="本地资料".encode(),
    )

    assert response.status_code == 201
    assert response.json()["display_name"] == "我的资料.txt"
    assert service.uploads == [("我的资料.txt", "本地资料".encode())]


def test_rejects_oversized_uploads_before_the_service_runs():
    service = FakeKnowledgeService()
    client = _client(service)

    response = client.post(
        "/v1/knowledge",
        params={"filename": "large.txt"},
        headers={**AUTH, "Content-Length": str(20 * 1024 * 1024 + 1)},
        content=b"small",
    )

    assert response.status_code == 413
    assert service.uploads == []


def test_deletes_an_imported_document():
    service = FakeKnowledgeService()
    client = _client(service)

    response = client.delete("/v1/knowledge/7", headers=AUTH)

    assert response.status_code == 204
    assert service.deleted == [7]
    assert client.delete("/v1/knowledge/999", headers=AUTH).status_code == 404


def test_cancels_the_active_import_without_waiting_for_the_write_lock():
    service = FakeKnowledgeService()
    client = _client(service)

    response = client.delete("/v1/knowledge/import", headers=AUTH)

    assert response.status_code == 204
    assert service.cancelled is True


def test_inspects_source_labelled_chunk_excerpts_without_filesystem_paths():
    client = _client(FakeKnowledgeService())

    response = client.get("/v1/knowledge/7/chunks?limit=4", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == [
        {"id": 11, "ordinal": 0, "page_number": 2, "content": "这是可核对的来源片段"}
    ]
    assert "source_path" not in response.text


class FakeEmbedder:
    def encode(self, texts: tuple[str, ...]) -> np.ndarray:
        vectors = np.zeros((len(texts), 512), dtype=np.float32)
        vectors[:, 0] = 1
        return vectors


@pytest.mark.asyncio
async def test_local_service_imports_lists_and_deletes_without_exposing_a_real_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("VOXAGENT_DATA_ROOT", str(tmp_path))
    database_path = tmp_path / "data" / "voxagent.db"
    connection = open_database(database_path)
    migrate(connection)
    connection.close()
    upload_temp = tmp_path / "cache" / "uploads"
    service = LocalKnowledgeService(database_path, upload_temp, FakeEmbedder())

    imported = await service.import_upload("我的资料.txt", "可检索的本地资料".encode())
    documents = await service.list_documents()

    assert imported["created"] is True
    assert documents[0]["display_name"] == "我的资料.txt"
    assert "source_path" not in documents[0]
    assert not tuple(upload_temp.glob("**/*"))
    check = open_database(database_path)
    stored = check.execute("SELECT source_path FROM documents").fetchone()[0]
    check.close()
    assert stored == "browser-upload:我的资料.txt"
    assert await service.delete_document(int(imported["document_id"])) is True
    assert await service.list_documents() == ()


@pytest.mark.asyncio
async def test_cancelled_local_import_leaves_no_partial_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BlockingEmbedder(FakeEmbedder):
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()

        def encode(self, texts: tuple[str, ...]) -> np.ndarray:
            self.started.set()
            self.release.wait(timeout=2)
            return super().encode(texts)

    monkeypatch.setenv("VOXAGENT_DATA_ROOT", str(tmp_path))
    database_path = tmp_path / "data" / "voxagent.db"
    connection = open_database(database_path)
    migrate(connection)
    connection.close()
    embedder = BlockingEmbedder()
    service = LocalKnowledgeService(
        database_path, tmp_path / "cache" / "uploads", embedder
    )
    import_task = asyncio.create_task(
        service.import_upload("资料.txt", "足够长的本地资料".encode())
    )
    assert await asyncio.to_thread(embedder.started.wait, 1)

    assert await service.cancel_import() is True
    embedder.release.set()
    with pytest.raises(ImportCancelled):
        await import_task

    assert await service.list_documents() == ()


@pytest.mark.asyncio
async def test_import_can_be_cancelled_while_waiting_for_the_shared_write_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VOXAGENT_DATA_ROOT", str(tmp_path))
    database_path = tmp_path / "data" / "voxagent.db"
    connection = open_database(database_path)
    migrate(connection)
    connection.close()
    gate = asyncio.Lock()
    await gate.acquire()
    service = LocalKnowledgeService(
        database_path, tmp_path / "cache" / "uploads", FakeEmbedder(), gate
    )
    import_task = asyncio.create_task(
        service.import_upload("排队资料.txt", "本地资料".encode())
    )
    await asyncio.sleep(0)

    assert await service.cancel_import() is True
    gate.release()
    with pytest.raises(ImportCancelled):
        await import_task

    assert await service.list_documents() == ()
