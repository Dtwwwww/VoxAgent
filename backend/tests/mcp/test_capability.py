from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import json
import os
import sqlite3
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

from voxagent.config import AppPaths
from voxagent.db.connection import open_database
from voxagent.db.migrations import migrate
from voxagent.tools.confirmation import arguments_sha256
from voxagent.tools.repository import ToolRepository
from voxagent.tools.schema import PermissionLevel, ToolCall

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
KEY = b"k" * 32
TOOL = "reminder.create"
ARGUMENTS = {"title": "提醒我喝水", "due_at_utc": None}


@pytest.fixture
def capability():
    return importlib.import_module("voxagent.mcp.capability")


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    paths = AppPaths.from_root(tmp_path / "runtime")
    monkeypatch.setenv("VOXAGENT_DATA_ROOT", str(paths.root))
    connection = open_database(paths.data / "voxagent.db")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def request_id(database: sqlite3.Connection) -> int:
    repository = ToolRepository(database)
    digest = arguments_sha256(ARGUMENTS)
    record = repository.create_request(
        "session", 1, ToolCall(call_id="call", name=TOOL, arguments=ARGUMENTS),
        PermissionLevel.L1, digest, NOW,
    )
    confirmation = repository.create_confirmation(record.id, digest, NOW)
    assert repository.consume_confirmation(confirmation.confirmation_id, digest, "session", 1, NOW)
    return record.id


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _payload(token: str) -> dict:
    part = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def _sign(payload: object = None, *, raw: bytes | None = None) -> str:
    if raw is None:
        raw = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    return f"{_encode(raw)}.{_encode(hmac.digest(KEY, raw, 'sha256'))}"


def _reject_before_executor(capability, verifier, token, tool=TOOL, arguments=None, now=NOW):
    executed = []
    with pytest.raises(capability.CapabilityError) as error:
        verifier.consume(token, tool, ARGUMENTS if arguments is None else arguments, now)
        executed.append("business executor")
    assert executed == []
    rendered = "".join(traceback.format_exception(error.value))
    if isinstance(token, str) and len(token) > 16:
        assert token not in rendered
    assert KEY.hex() not in rendered
    assert KEY.decode() not in rendered
    return error.value


def test_issue_is_canonical_bound_and_consumption_returns_nonce(capability, database, request_id):
    issuer = capability.CapabilityIssuer(KEY)
    token = issuer.issue(request_id, TOOL, ARGUMENTS, NOW)
    payload = _payload(token)
    assert set(payload) == {
        "tool_request_id", "tool_name", "arguments_sha256", "nonce", "expires_at_utc"
    }
    assert payload["tool_request_id"] == request_id
    assert payload["tool_name"] == TOOL
    assert payload["arguments_sha256"] == arguments_sha256(ARGUMENTS)
    assert datetime.fromisoformat(payload["expires_at_utc"]) == NOW + timedelta(seconds=30)
    assert token == _sign(payload)
    assert "=" not in token
    assert len(token) <= 2048
    assert ARGUMENTS["title"] not in token
    assert _payload(issuer.issue(request_id, TOOL, ARGUMENTS, NOW))["nonce"] != payload["nonce"]
    verifier = capability.CapabilityVerifier(database, KEY)
    reordered = dict(reversed(list(ARGUMENTS.items())))
    assert verifier.consume(token, TOOL, reordered, NOW) == payload["nonce"]
    row = database.execute("SELECT * FROM mcp_capability_nonces").fetchone()
    assert row["tool_request_id"] == request_id
    assert row["nonce"] == payload["nonce"]
    assert datetime.fromisoformat(row["consumed_at_utc"]) == NOW
    assert not database.in_transaction


@pytest.mark.parametrize(
    "failure", ["signature", "expired", "boundary", "tool", "arguments", "replay"]
)
def test_security_failures_precede_executor(capability, database, request_id, failure, caplog):
    token = capability.CapabilityIssuer(KEY).issue(request_id, TOOL, ARGUMENTS, NOW)
    verifier = capability.CapabilityVerifier(database, KEY)
    tool, arguments, now = TOOL, ARGUMENTS, NOW
    if failure == "signature":
        token = token.split(".")[0] + "." + _encode(b"x" * 32)
    elif failure == "expired":
        now += timedelta(seconds=31)
    elif failure == "boundary":
        now += timedelta(seconds=30)
    elif failure == "tool":
        tool = "reminder.complete"
    elif failure == "arguments":
        arguments = {**ARGUMENTS, "title": "other"}
    elif failure == "replay":
        verifier.consume(token, tool, arguments, now)
    _reject_before_executor(capability, verifier, token, tool, arguments, now)
    assert database.execute("SELECT COUNT(*) FROM mcp_capability_nonces").fetchone()[0] == (
        1 if failure == "replay" else 0
    )
    assert token not in caplog.text
    assert KEY.decode() not in caplog.text
    audit = json.dumps([tuple(row) for row in database.execute("SELECT * FROM tool_audit")])
    assert token not in audit
    assert KEY.hex() not in audit


@pytest.mark.parametrize(
    "token", [None, 123, "", "x" * 2049, ".", "a.b.c", "a.b", "!!.@@", "é.é", "e30=.AAAA"],
    ids=["none", "integer", "empty", "oversized", "empty-parts", "three-parts", "bad-base64",
         "symbols", "unicode", "padded"],
)
def test_malformed_token_rejected_without_database_access(capability, database, token):
    statements = []
    database.set_trace_callback(statements.append)
    _reject_before_executor(capability, capability.CapabilityVerifier(database, KEY), token)
    assert statements == []


@pytest.mark.parametrize("change", [
    {"extra": "forbidden"}, {"tool_request_id": True}, {"tool_request_id": "1"},
    {"tool_request_id": 0}, {"tool_request_id": 2**80}, {"tool_name": 3},
    {"nonce": ""}, {"nonce": 1}, {"arguments_sha256": "A" * 64},
    {"expires_at_utc": "2026-09-11T12:00:30"},
    {"expires_at_utc": "2026-09-11T20:00:30+08:00"},
    {"expires_at_utc": "2026-09-11T12:00:31Z"},
    {"expires_at_utc": "not-a-date"},
])
def test_signed_invalid_payload_rejected_before_database(capability, database, request_id, change):
    payload = _payload(capability.CapabilityIssuer(KEY).issue(request_id, TOOL, ARGUMENTS, NOW))
    payload.update(change)
    statements = []
    database.set_trace_callback(statements.append)
    _reject_before_executor(
        capability, capability.CapabilityVerifier(database, KEY), _sign(payload)
    )
    assert statements == []


@pytest.mark.parametrize(
    "raw", [b"null", b"[]", b"{}", b"invalid-json", b"\xff", b'{"nonce":NaN}', b"[" * 1000],
    ids=["null", "array", "missing-fields", "invalid-json", "invalid-utf8", "nan", "depth"],
)
def test_signed_malformed_json_rejected(capability, database, raw):
    _reject_before_executor(
        capability, capability.CapabilityVerifier(database, KEY), _sign(raw=raw)
    )


def test_duplicate_fields_and_noncanonical_json_rejected(capability, database, request_id):
    payload = _payload(capability.CapabilityIssuer(KEY).issue(request_id, TOOL, ARGUMENTS, NOW))
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    duplicate = raw[:-1] + b',"tool_request_id":1}'
    verifier = capability.CapabilityVerifier(database, KEY)
    _reject_before_executor(capability, verifier, _sign(raw=duplicate))
    _reject_before_executor(capability, verifier, _sign(raw=b" " + raw))


@pytest.mark.parametrize("mutation", [
    "missing", "pending", "awaiting_confirmation", "succeeded", "failed", "denied", "expired",
    "no_confirmation", "unconsumed", "denied_confirmation", "confirmation_hash",
    "request_hash", "request_tool",
])
def test_request_must_still_have_matching_user_approval(capability, database, request_id, mutation):
    token = capability.CapabilityIssuer(KEY).issue(request_id, TOOL, ARGUMENTS, NOW)
    if mutation == "missing":
        database.execute("DELETE FROM tool_requests")
    elif mutation == "no_confirmation":
        database.execute("DELETE FROM tool_confirmations")
    elif mutation == "unconsumed":
        database.execute("UPDATE tool_confirmations SET consumed_at_utc = NULL")
    elif mutation == "denied_confirmation":
        database.execute("UPDATE tool_confirmations SET decision = 'denied'")
    elif mutation == "confirmation_hash":
        database.execute("UPDATE tool_confirmations SET arguments_sha256 = ?", ("a" * 64,))
    elif mutation == "request_hash":
        database.execute("UPDATE tool_requests SET arguments_sha256 = ?", ("a" * 64,))
    elif mutation == "request_tool":
        database.execute("UPDATE tool_requests SET tool_name = 'reminder.complete'")
    else:
        database.execute("UPDATE tool_requests SET status = ?", (mutation,))
    _reject_before_executor(capability, capability.CapabilityVerifier(database, KEY), token)
    assert database.execute("SELECT COUNT(*) FROM mcp_capability_nonces").fetchone()[0] == 0


def test_two_connections_consume_exactly_once(capability, database, request_id):
    token = capability.CapabilityIssuer(KEY).issue(request_id, TOOL, ARGUMENTS, NOW)
    path = Path(database.execute("PRAGMA database_list").fetchone()[2])
    barrier = Barrier(2)

    def consume():
        connection = open_database(path)

        def synchronize_writers(statement):
            if statement == "BEGIN IMMEDIATE":
                barrier.wait(timeout=5)

        try:
            verifier = capability.CapabilityVerifier(connection, KEY)
            # Both connections finish preflight and compete for the same SQLite write lock.
            connection.set_trace_callback(synchronize_writers)
            try:
                verifier.consume(token, TOOL, ARGUMENTS, NOW)
            except capability.CapabilityError:
                return "rejected"
            return "executed"
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consume(), range(2)))
    assert sorted(results) == ["executed", "rejected"]
    assert database.execute("SELECT COUNT(*) FROM mcp_capability_nonces").fetchone()[0] == 1


def test_approval_is_rechecked_under_immediate_transaction(capability, database, request_id):
    token = capability.CapabilityIssuer(KEY).issue(request_id, TOOL, ARGUMENTS, NOW)
    path = Path(database.execute("PRAGMA database_list").fetchone()[2])
    other = open_database(path)
    changed = False

    def revoke_on_begin(statement):
        nonlocal changed
        if statement == "BEGIN IMMEDIATE" and not changed:
            changed = True
            other.execute("UPDATE tool_requests SET status = 'denied'")

    try:
        database.set_trace_callback(revoke_on_begin)
        _reject_before_executor(capability, capability.CapabilityVerifier(database, KEY), token)
    finally:
        database.set_trace_callback(None)
        other.close()
    assert changed
    assert database.execute("SELECT COUNT(*) FROM mcp_capability_nonces").fetchone()[0] == 0
    assert not database.in_transaction


def test_restart_rotates_key_and_explicit_child_environment_only(capability, database, request_id):
    environment_before = dict(os.environ)
    first = capability.CapabilityIssuer()
    second = capability.CapabilityIssuer()
    child_env = first.child_environment()
    assert set(child_env) == {"VOXAGENT_MCP_CAPABILITY_KEY"}
    assert len(bytes.fromhex(child_env["VOXAGENT_MCP_CAPABILITY_KEY"])) == 32
    assert child_env != second.child_environment()
    assert dict(os.environ) == environment_before
    token = first.issue(request_id, TOOL, ARGUMENTS, NOW)
    restarted = capability.CapabilityVerifier.from_environment(database, second.child_environment())
    _reject_before_executor(capability, restarted, token)
    verifier = capability.CapabilityVerifier.from_environment(database, child_env)
    assert verifier.consume(token, TOOL, ARGUMENTS, NOW) == _payload(token)["nonce"]
    for obj in (first, second, verifier):
        assert child_env["VOXAGENT_MCP_CAPABILITY_KEY"] not in repr(obj)


def test_default_key_uses_32_cryptographically_random_bytes(capability, monkeypatch):
    requested = []

    def token_bytes(size):
        requested.append(size)
        return KEY

    monkeypatch.setattr(capability.secrets, "token_bytes", token_bytes)
    issuer = capability.CapabilityIssuer()
    assert requested == [32]
    assert issuer.child_environment() == {"VOXAGENT_MCP_CAPABILITY_KEY": KEY.hex()}


@pytest.mark.parametrize("value", [None, "", "not-hex-secret", "00" * 31, "00" * 33])
def test_child_rejects_missing_or_invalid_key(capability, database, value):
    env = {} if value is None else {"VOXAGENT_MCP_CAPABILITY_KEY": value}
    with pytest.raises(capability.CapabilityError) as error:
        capability.CapabilityVerifier.from_environment(database, env)
    assert "not-hex-secret" not in str(error.value)


@pytest.mark.parametrize(
    "now", [NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=8)))]
)
def test_both_sides_require_explicit_utc(capability, database, request_id, now):
    issuer = capability.CapabilityIssuer(KEY)
    with pytest.raises(capability.CapabilityError):
        issuer.issue(request_id, TOOL, ARGUMENTS, now)
    token = issuer.issue(request_id, TOOL, ARGUMENTS, NOW)
    _reject_before_executor(
        capability, capability.CapabilityVerifier(database, KEY), token, now=now
    )


def test_invalid_arguments_are_rejected_without_leaking_values(capability, database, request_id):
    issuer = capability.CapabilityIssuer(KEY)
    token = issuer.issue(request_id, TOOL, ARGUMENTS, NOW)
    arguments = {"sensitive-value": float("nan")}
    with pytest.raises(capability.CapabilityError):
        issuer.issue(request_id, TOOL, arguments, NOW)
    _reject_before_executor(
        capability, capability.CapabilityVerifier(database, KEY), token, arguments=arguments
    )


def test_hmac_is_compared_before_json_or_database(capability, database, monkeypatch):
    compared = []
    original = hmac.compare_digest

    def compare(left, right):
        compared.append((len(left), len(right)))
        return original(left, right)

    monkeypatch.setattr(capability.hmac, "compare_digest", compare)
    token = f"{_encode(b'not-json')}.{_encode(b'x' * 32)}"
    statements = []
    database.set_trace_callback(statements.append)
    _reject_before_executor(capability, capability.CapabilityVerifier(database, KEY), token)
    assert compared == [(hashlib.sha256().digest_size, 32)]
    assert statements == []
