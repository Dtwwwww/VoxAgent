from __future__ import annotations

import base64
import hmac
import json
import re
import secrets
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from voxagent.tools.confirmation import arguments_sha256, canonical_arguments

_KEY_ENVIRONMENT_VARIABLE = "VOXAGENT_MCP_CAPABILITY_KEY"
_MAX_TOKEN_LENGTH = 2048
_LIFETIME = timedelta(seconds=30)
_PAYLOAD_FIELDS = frozenset(
    {"tool_request_id", "tool_name", "arguments_sha256", "nonce", "expires_at_utc"}
)
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+")
_HEX_KEY = re.compile(r"[0-9a-f]{64}")
_TOOL_NAME = re.compile(r"[a-z][a-z0-9_.]{2,63}")
_NONCE = re.compile(r"[A-Za-z0-9_-]{16,128}")


class CapabilityError(RuntimeError):
    """A deliberately fixed, transport-safe error without credential details."""

    def __init__(self) -> None:
        super().__init__("mcp_capability_rejected")
        self.code = "mcp_capability_rejected"


def _validate_key(key: bytes) -> bytes:
    if not isinstance(key, bytes) or len(key) != 32:
        raise CapabilityError()
    return key


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise CapabilityError()
    return value.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    if not _BASE64URL.fullmatch(value):
        raise CapabilityError()
    decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if _encode(decoded) != value:
        raise CapabilityError()
    return decoded


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise CapabilityError()
        result[name] = value
    return result


def _payload(raw: bytes) -> dict[str, object]:
    payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_FIELDS:
        raise CapabilityError()
    request_id = payload["tool_request_id"]
    if type(request_id) is not int or not 1 <= request_id <= 2**63 - 1:
        raise CapabilityError()
    for name, pattern in (
        ("tool_name", _TOOL_NAME),
        ("arguments_sha256", _HEX_KEY),
        ("nonce", _NONCE),
    ):
        if not isinstance(payload[name], str) or not pattern.fullmatch(payload[name]):
            raise CapabilityError()
    if not isinstance(payload["expires_at_utc"], str):
        raise CapabilityError()
    if canonical_arguments(payload) != raw:
        raise CapabilityError()
    return payload


class CapabilityIssuer:
    """Create once per parent-process startup; explicitly pass its child environment."""

    def __init__(self, key: bytes | None = None) -> None:
        self._key = _validate_key(secrets.token_bytes(32) if key is None else key)

    def child_environment(self) -> dict[str, str]:
        """Return credential entries for a child environment without mutating os.environ."""
        return {_KEY_ENVIRONMENT_VARIABLE: self._key.hex()}

    def issue(
        self,
        tool_request_id: int,
        tool_name: str,
        arguments: dict[str, object],
        now_utc: datetime,
    ) -> str:
        try:
            if not isinstance(arguments, dict):
                raise CapabilityError()
            payload = {
                "tool_request_id": tool_request_id,
                "tool_name": tool_name,
                "arguments_sha256": arguments_sha256(arguments),
                "nonce": secrets.token_urlsafe(24),
                "expires_at_utc": _utc_text(_utc(now_utc) + _LIFETIME),
            }
            raw = canonical_arguments(payload)
            _payload(raw)
            token = f"{_encode(raw)}.{_encode(hmac.digest(self._key, raw, 'sha256'))}"
            if len(token) > _MAX_TOKEN_LENGTH:
                raise CapabilityError()
            return token
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise CapabilityError() from None


class CapabilityVerifier:
    def __init__(self, connection: sqlite3.Connection, key: bytes) -> None:
        self._connection = connection
        self._key = _validate_key(key)

    @classmethod
    def from_environment(
        cls, connection: sqlite3.Connection, environment: Mapping[str, str]
    ) -> CapabilityVerifier:
        encoded = environment.get(_KEY_ENVIRONMENT_VARIABLE)
        if not isinstance(encoded, str) or not _HEX_KEY.fullmatch(encoded):
            raise CapabilityError()
        return cls(connection, bytes.fromhex(encoded))

    def consume(
        self,
        token: str,
        tool_name: str,
        arguments: dict[str, object],
        now_utc: datetime,
    ) -> str:
        """Atomically consume before any executor runs; return the consumed nonce."""
        try:
            if not isinstance(token, str) or len(token) > _MAX_TOKEN_LENGTH:
                raise CapabilityError()
            parts = token.split(".")
            if len(parts) != 2:
                raise CapabilityError()
            raw, signature = (_decode(part) for part in parts)
            expected = hmac.digest(self._key, raw, "sha256")
            if not hmac.compare_digest(expected, signature):
                raise CapabilityError()
            payload = _payload(raw)
            if not isinstance(arguments, dict) or payload["tool_name"] != tool_name:
                raise CapabilityError()
            if payload["arguments_sha256"] != arguments_sha256(arguments):
                raise CapabilityError()
            now = _utc(now_utc)
            expires = _utc(datetime.fromisoformat(payload["expires_at_utc"]))
            if not timedelta(0) < expires - now <= _LIFETIME:
                raise CapabilityError()
            if not self._approved(payload):
                raise CapabilityError()
            self._consume_nonce(payload, now)
            return payload["nonce"]
        except (ValueError, TypeError, OverflowError, RecursionError, sqlite3.Error):
            raise CapabilityError() from None

    def _approved(self, payload: dict[str, object]) -> bool:
        return self._connection.execute(
            """
            SELECT 1 FROM tool_requests AS request
            JOIN tool_confirmations AS confirmation ON confirmation.tool_request_id = request.id
            WHERE request.id = ? AND request.tool_name = ? AND request.arguments_sha256 = ?
              AND request.status = 'running'
              AND confirmation.arguments_sha256 = request.arguments_sha256
              AND confirmation.decision = 'approved'
              AND confirmation.consumed_at_utc IS NOT NULL
            LIMIT 1
            """,
            (payload["tool_request_id"], payload["tool_name"], payload["arguments_sha256"]),
        ).fetchone() is not None

    def _consume_nonce(self, payload: dict[str, object], now: datetime) -> None:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            # A preflight read alone races revocation; recheck while holding the write lock.
            if not self._approved(payload):
                raise CapabilityError()
            self._connection.execute(
                """
                INSERT INTO mcp_capability_nonces(
                    nonce, tool_request_id, expires_at_utc, consumed_at_utc
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    payload["nonce"], payload["tool_request_id"],
                    payload["expires_at_utc"], _utc_text(now),
                ),
            )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
