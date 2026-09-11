from __future__ import annotations

import asyncio
import base64
import io
import json
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from voxagent.api.app import create_app
from voxagent.api.data import LocalDataService
from voxagent.db.backup import DailyBackupManager

SESSION_TOKEN = base64.urlsafe_b64encode(b"d" * 32).decode("ascii").rstrip("=")
AUTH = {"Authorization": f"Bearer {SESSION_TOKEN}"}


class FakeOrchestrator:
    def __init__(self) -> None:
        self.model_id = "qwen-local"
        self.state = SimpleNamespace(session_id=uuid4())
        self.voice_catalog = SimpleNamespace(public_profiles=lambda: ())
        self.reset_count = 0
        self.resume_count = 0

    async def stop(self) -> None:
        return None

    async def reset_conversation(self) -> None:
        self.reset_count += 1

    async def resume_after_reset(self) -> None:
        self.resume_count += 1


def _database(path: Path) -> None:
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_version(version INTEGER NOT NULL);
        INSERT INTO schema_version VALUES (2);
        CREATE TABLE conversations(id INTEGER PRIMARY KEY, title TEXT);
        CREATE TABLE messages(id INTEGER PRIMARY KEY, content TEXT);
        CREATE TABLE memories(id INTEGER PRIMARY KEY, content TEXT, embedding BLOB);
        CREATE TABLE documents(id INTEGER PRIMARY KEY, display_name TEXT, source_path TEXT);
        CREATE TABLE document_chunks(id INTEGER PRIMARY KEY, content TEXT, embedding BLOB);
        CREATE TABLE audit_events(id INTEGER PRIMARY KEY, event_type TEXT);
        CREATE TABLE persona_config(id INTEGER PRIMARY KEY, config_json TEXT);
        INSERT INTO memories(content, embedding) VALUES ('喜欢茶', x'0000');
        INSERT INTO documents(display_name, source_path) VALUES ('资料.txt', 'D:/private.txt');
        """
    )
    connection.commit()
    connection.close()


def _client(tmp_path: Path) -> tuple[TestClient, LocalDataService, Path]:
    database_path = tmp_path / "data" / "voxagent.db"
    _database(database_path)
    service = LocalDataService(database_path, tmp_path / "data")
    app = create_app(
        lambda: FakeOrchestrator(), SESSION_TOKEN, data_service=service
    )
    return TestClient(app), service, database_path


def test_export_is_authenticated_one_time_utf8_json_without_vector_blobs(
    tmp_path: Path,
) -> None:
    client, _, _ = _client(tmp_path)
    assert client.post("/v1/data/export").status_code == 401

    ticket = client.post("/v1/data/export", headers=AUTH)
    assert ticket.status_code == 201
    assert "path" not in ticket.text
    download_id = ticket.json()["download_id"]
    downloaded = client.get(f"/v1/data/export/{download_id}", headers=AUTH)

    assert downloaded.status_code == 200
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        payload = json.loads(archive.read("voxagent-export.json"))
    assert payload["schema_version"] == 2
    assert payload["memories"][0]["content"] == "喜欢茶"
    assert "embedding" not in payload["memories"][0]
    assert client.get(f"/v1/data/export/{download_id}", headers=AUTH).status_code == 404


def test_full_reset_requires_exact_phrase_and_removes_user_rows_and_backups(
    tmp_path: Path,
) -> None:
    client, _, database_path = _client(tmp_path)
    backup_directory = tmp_path / "data" / "backups"
    backup_directory.mkdir(parents=True)
    (backup_directory / "voxagent-2026-09-04.db").write_bytes(b"backup")

    wrong = client.request(
        "DELETE", "/v1/data", headers=AUTH, json={"confirmation": "全部删除"}
    )
    accepted = client.request(
        "DELETE",
        "/v1/data",
        headers=AUTH,
        json={"confirmation": "删除声灵全部本地数据"},
    )

    assert wrong.status_code == 422
    assert accepted.status_code == 204
    connection = sqlite3.connect(database_path)
    for table in (
        "conversations",
        "messages",
        "memories",
        "documents",
        "document_chunks",
        "audit_events",
        "persona_config",
    ):
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    connection.close()
    assert not (backup_directory / "voxagent-2026-09-04.db").exists()


def test_lists_and_deletes_backups_without_exposing_paths(tmp_path: Path) -> None:
    client, service, database_path = _client(tmp_path)
    connection = sqlite3.connect(database_path)
    manager = DailyBackupManager(tmp_path / "data" / "backups")
    manager.create(connection, __import__("datetime").date(2026, 9, 4))
    connection.close()

    listed = client.get("/v1/backups", headers=AUTH)

    assert listed.status_code == 200
    assert listed.json()[0]["filename"] == "voxagent-2026-09-04.db"
    assert "path" not in listed.text
    assert client.delete(
        "/v1/backups/voxagent-2026-09-04.db", headers=AUTH
    ).status_code == 204
    assert service.list_backups() == ()


@pytest.mark.asyncio
async def test_reset_waits_for_the_shared_application_mutation_gate(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "data" / "voxagent.db"
    _database(database_path)
    gate = asyncio.Lock()
    service = LocalDataService(database_path, tmp_path / "data", gate)
    await gate.acquire()

    reset = asyncio.create_task(service.reset_all())
    await asyncio.sleep(0.01)
    assert not reset.done()

    gate.release()
    await reset


def test_full_reset_clears_the_active_orchestrator_history(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "voxagent.db"
    _database(database_path)
    orchestrator = FakeOrchestrator()
    service = LocalDataService(database_path, tmp_path / "data")
    app = create_app(lambda: orchestrator, SESSION_TOKEN, data_service=service)

    with TestClient(app) as client:
        with client.websocket_connect(f"/v1/voice?token={SESSION_TOKEN}") as socket:
            socket.send_json({"type": "session.start"})
            socket.receive_json()
            socket.receive_json()
            response = client.request(
                "DELETE",
                "/v1/data",
                headers=AUTH,
                json={"confirmation": "删除声灵全部本地数据"},
            )

    assert response.status_code == 204
    assert orchestrator.reset_count == 1
    assert orchestrator.resume_count == 1
