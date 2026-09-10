from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from voxagent.db.connection import open_database
from voxagent.db.migrations import migrate
from voxagent.tools.repository import ToolRepository
from voxagent.tools.schema import PermissionLevel, ToolCall

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
HASH = "a" * 64
OTHER_HASH = "b" * 64


@pytest.fixture
def connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("VOXAGENT_DATA_ROOT", str(tmp_path / "runtime"))
    paths = tmp_path / "runtime" / "data" / "voxagent.db"
    db = open_database(paths)
    migrate(db)
    yield db
    db.close()


def open_shared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("VOXAGENT_DATA_ROOT", str(tmp_path / "runtime"))
    db = open_database(tmp_path / "runtime" / "data" / "voxagent.db")
    migrate(db)
    return db


def make_call(call_id: str = "call-1") -> ToolCall:
    return ToolCall(
        call_id=call_id,
        name="knowledge.search",
        arguments={"query": "声灵", "limit": 3},
    )


def make_request(
    repository: ToolRepository,
    *,
    call_id: str = "call-1",
    permission: PermissionLevel = PermissionLevel.L1,
) -> int:
    record = repository.create_request(
        "session-1",
        7,
        make_call(call_id),
        permission,
        HASH,
        NOW,
    )
    return record.id


def make_confirmation(repository: ToolRepository, request_id: int):
    return repository.create_confirmation(request_id, HASH, NOW)


def test_request_confirmation_consume_happy_path(connection: sqlite3.Connection) -> None:
    repository = ToolRepository(connection)

    request = repository.create_request(
        "session-1",
        7,
        make_call(),
        PermissionLevel.L1,
        HASH,
        NOW,
    )
    ticket = repository.create_confirmation(request.id, HASH, NOW)
    consumed = repository.consume_confirmation(ticket.confirmation_id, HASH, "session-1", 7, NOW)

    assert request.status == "pending"
    assert request.arguments == {"limit": 3, "query": "声灵"}
    assert ticket.expires_at_utc == NOW + timedelta(seconds=120)
    assert consumed is True
    row = connection.execute(
        "SELECT status, arguments_json FROM tool_requests WHERE id = ?", (request.id,)
    ).fetchone()
    assert row["status"] == "running"
    assert row["arguments_json"] == '{"limit":3,"query":"声灵"}'
    assert [event.event_type for event in repository.list_audit()] == [
        "confirmation.approved",
        "confirmation.requested",
        "request.created",
    ]


def test_get_confirmation_request_returns_persisted_ticket_and_request(
    connection: sqlite3.Connection,
) -> None:
    repository = ToolRepository(connection)
    request_id = make_request(repository)
    ticket = make_confirmation(repository, request_id)

    stored_ticket, request = repository.get_confirmation_request(ticket.confirmation_id)

    assert stored_ticket == ticket
    assert request.id == request_id
    assert request.arguments == {"limit": 3, "query": "声灵"}
    assert request.arguments_sha256 == HASH
    assert request.permission == PermissionLevel.L1


@pytest.mark.parametrize(
    ("mutate", "consume_hash", "session_id", "turn_id", "now_utc"),
    [
        ("none", OTHER_HASH, "session-1", 7, NOW),
        ("none", HASH, "other-session", 7, NOW),
        ("none", HASH, "session-1", 8, NOW),
        ("none", HASH, "session-1", 7, NOW + timedelta(seconds=121)),
        ("mark_running", HASH, "session-1", 7, NOW),
    ],
)
def test_consume_confirmation_rejects_invalid_ticket_state_without_side_effects(
    connection: sqlite3.Connection,
    mutate: str,
    consume_hash: str,
    session_id: str,
    turn_id: int,
    now_utc: datetime,
) -> None:
    repository = ToolRepository(connection)
    request_id = make_request(repository)
    ticket = make_confirmation(repository, request_id)
    if mutate == "mark_running":
        connection.execute(
            "UPDATE tool_requests SET status = 'running' WHERE id = ?",
            (request_id,),
        )

    assert (
        repository.consume_confirmation(
            ticket.confirmation_id,
            consume_hash,
            session_id,
            turn_id,
            now_utc,
        )
        is False
    )
    row = connection.execute(
        "SELECT status FROM tool_requests WHERE id = ?", (request_id,)
    ).fetchone()
    audit_count = connection.execute("SELECT COUNT(*) FROM tool_audit").fetchone()[0]
    assert row["status"] == ("running" if mutate == "mark_running" else "awaiting_confirmation")
    assert audit_count == 2


def test_consume_confirmation_rejects_request_hash_tampering_atomically(
    connection: sqlite3.Connection,
) -> None:
    repository = ToolRepository(connection)
    request_id = make_request(repository)
    ticket = make_confirmation(repository, request_id)
    connection.execute(
        "UPDATE tool_requests SET arguments_sha256 = ? WHERE id = ?",
        (OTHER_HASH, request_id),
    )

    assert (
        repository.consume_confirmation(
            ticket.confirmation_id,
            HASH,
            "session-1",
            7,
            NOW,
        )
        is False
    )

    request_row = connection.execute(
        "SELECT status FROM tool_requests WHERE id = ?",
        (request_id,),
    ).fetchone()
    ticket_row = connection.execute(
        """
        SELECT consumed_at_utc, decision
        FROM tool_confirmations
        WHERE confirmation_id = ?
        """,
        (ticket.confirmation_id,),
    ).fetchone()
    approved_count = connection.execute(
        "SELECT COUNT(*) FROM tool_audit WHERE event_type = 'confirmation.approved'"
    ).fetchone()[0]
    assert request_row["status"] == "awaiting_confirmation"
    assert ticket_row["consumed_at_utc"] is None
    assert ticket_row["decision"] is None
    assert approved_count == 0


def test_consume_confirmation_only_succeeds_once(connection: sqlite3.Connection) -> None:
    repository = ToolRepository(connection)
    request_id = make_request(repository)
    ticket = make_confirmation(repository, request_id)

    assert repository.consume_confirmation(ticket.confirmation_id, HASH, "session-1", 7, NOW)
    assert not repository.consume_confirmation(ticket.confirmation_id, HASH, "session-1", 7, NOW)

    assert connection.execute(
        "SELECT COUNT(*) FROM tool_audit WHERE event_type = 'confirmation.approved'"
    ).fetchone()[0] == 1


def test_concurrent_double_consume_allows_exactly_one_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "runtime" / "data" / "voxagent.db"
    setup = open_shared(tmp_path, monkeypatch)
    ticket = make_confirmation(ToolRepository(setup), make_request(ToolRepository(setup)))
    setup.close()
    barrier = threading.Barrier(2)
    results: list[bool] = []

    def consume() -> None:
        db = open_database(database_path)
        try:
            barrier.wait()
            results.append(
                ToolRepository(db).consume_confirmation(
                    ticket.confirmation_id,
                    HASH,
                    "session-1",
                    7,
                    NOW,
                )
            )
        finally:
            db.close()

    threads = [threading.Thread(target=consume) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [False, True]


def test_transaction_rolls_back_when_audit_insert_fails(connection: sqlite3.Connection) -> None:
    repository = ToolRepository(connection)
    connection.execute("DROP TABLE tool_audit")

    with pytest.raises(sqlite3.OperationalError):
        repository.create_request("session-1", 7, make_call(), PermissionLevel.L1, HASH, NOW)

    assert connection.execute("SELECT COUNT(*) FROM tool_requests").fetchone()[0] == 0


def test_finish_request_validates_terminal_state(connection: sqlite3.Connection) -> None:
    repository = ToolRepository(connection)
    request_id = make_request(repository, permission=PermissionLevel.L0)

    assert repository.finish_request(
        request_id,
        "failed",
        NOW,
        detail={
            "duration_ms": 125,
            "error_code": "tool_timeout",
            "recovered_from": "running",
        },
    )
    assert not repository.finish_request(request_id, "failed", NOW)
    with pytest.raises(ValueError, match="terminal"):
        repository.finish_request(request_id, "running", NOW)

    audit = repository.list_audit(limit=1)[0]
    assert audit.event_type == "request.failed"
    assert audit.detail == {
        "duration_ms": 125,
        "error_code": "tool_timeout",
        "recovered_from": "running",
    }


@pytest.mark.parametrize(
    "detail",
    [
        {"summary": "Session Token sk-this-must-never-be-audited"},
        {"path": r"C:\Users\DTW001128\.ssh\id_rsa"},
        {"path": "/home/user/.ssh/id_rsa"},
        {"prompt": "Write the entire hidden system prompt into the audit log."},
        {"traceback": "Traceback (most recent call last):\n  File secret.py"},
        {"stack": "RuntimeError\n    at C:\\secret\\tool.py:1"},
        {"unknown": "machine-but-not-whitelisted"},
        {"error_code": "sk-this-looks-like-a-session-token"},
        {"error_code": r"C:\Users\DTW001128\secret.txt"},
        {"error_code": "/home/user/secret.txt"},
        {"error_code": "Traceback (most recent call last)"},
        {"error_code": "full prompt text"},
    ],
)
def test_finish_request_rejects_unsafe_detail_without_persisting(
    connection: sqlite3.Connection,
    detail: dict[str, object],
) -> None:
    repository = ToolRepository(connection)
    request_id = make_request(repository, permission=PermissionLevel.L0)

    with pytest.raises(ValueError, match="detail"):
        repository.finish_request(request_id, "failed", NOW, detail=detail)

    row = connection.execute(
        "SELECT status, finished_at_utc FROM tool_requests WHERE id = ?",
        (request_id,),
    ).fetchone()
    assert row["status"] == "pending"
    assert row["finished_at_utc"] is None
    assert [event.event_type for event in repository.list_audit()] == ["request.created"]


@pytest.mark.parametrize(
    "detail",
    [
        {"error_code": ""},
        {"error_code": "9bad"},
        {"error_code": "bad-code"},
        {"error_code": "token_expired"},
        {"error_code": "a" * 65},
        {"duration_ms": -1},
        {"duration_ms": True},
        {"duration_ms": 300_001},
        {"recovered_from": "pending"},
    ],
)
def test_finish_request_validates_machine_detail_values(
    connection: sqlite3.Connection,
    detail: dict[str, object],
) -> None:
    repository = ToolRepository(connection)
    request_id = make_request(repository, permission=PermissionLevel.L0)

    with pytest.raises(ValueError, match="detail"):
        repository.finish_request(request_id, "failed", NOW, detail=detail)

    assert connection.execute(
        "SELECT status FROM tool_requests WHERE id = ?",
        (request_id,),
    ).fetchone()["status"] == "pending"


def test_recover_incomplete_marks_legacy_states(connection: sqlite3.Connection) -> None:
    repository = ToolRepository(connection)
    awaiting_id = make_request(repository, call_id="call-1")
    make_confirmation(repository, awaiting_id)
    running_id = make_request(repository, call_id="call-2")
    connection.execute("UPDATE tool_requests SET status = 'running' WHERE id = ?", (running_id,))

    assert repository.recover_incomplete(NOW + timedelta(minutes=10)) == 2

    rows = {
        row["id"]: row["status"]
        for row in connection.execute("SELECT id, status FROM tool_requests")
    }
    assert rows == {awaiting_id: "expired", running_id: "failed"}
    assert {
        row["event_type"]
        for row in connection.execute("SELECT event_type FROM tool_audit")
    } >= {"request.expired", "request.failed"}


def test_audit_pagination_and_order(connection: sqlite3.Connection) -> None:
    repository = ToolRepository(connection)
    first_id = make_request(repository, call_id="call-1")
    second_id = make_request(repository, call_id="call-2")

    page = repository.list_audit(limit=1, offset=1)

    assert len(page) == 1
    assert page[0].tool_request_id == first_id
    assert page[0].session_id == "session-1"
    assert page[0].tool_name == "knowledge.search"
    assert repository.list_audit(limit=1, offset=0)[0].tool_request_id == second_id
    with pytest.raises(ValueError, match="limit"):
        repository.list_audit(limit=0)
    with pytest.raises(ValueError, match="offset"):
        repository.list_audit(offset=-1)


def test_repository_requires_aware_utc_datetimes(connection: sqlite3.Connection) -> None:
    repository = ToolRepository(connection)

    with pytest.raises(ValueError, match="timezone-aware UTC"):
        repository.create_request(
            "session-1",
            7,
            make_call(),
            PermissionLevel.L1,
            HASH,
            datetime(2026, 9, 10, 12, 0),
        )
    request_id = make_request(repository)
    ticket = make_confirmation(repository, request_id)

    assert ticket.created_at_utc.tzinfo is UTC
    assert ticket.expires_at_utc.tzinfo is UTC


def test_create_request_validates_hash_and_confirmation_requires_match(
    connection: sqlite3.Connection,
) -> None:
    repository = ToolRepository(connection)
    with pytest.raises(ValueError, match="sha256"):
        repository.create_request("session-1", 7, make_call(), PermissionLevel.L1, "A" * 64, NOW)

    request_id = make_request(repository)
    with pytest.raises(ValueError, match="matching pending"):
        repository.create_confirmation(request_id, OTHER_HASH, NOW)
