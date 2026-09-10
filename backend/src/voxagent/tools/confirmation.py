from __future__ import annotations

import hashlib
import json
from datetime import datetime

from pydantic import ValidationError

from voxagent.tools.policy import PolicyContext, ToolPolicy
from voxagent.tools.registry import ToolRegistry, UnknownToolError
from voxagent.tools.repository import ConfirmationTicket, ToolRepository
from voxagent.tools.schema import PermissionLevel, ToolCall


class ConfirmationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonical_arguments(arguments: dict[str, object]) -> bytes:
    return json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def arguments_sha256(arguments: dict[str, object]) -> str:
    return hashlib.sha256(canonical_arguments(arguments)).hexdigest()


class ConfirmationService:
    def __init__(self, repository: ToolRepository, registry: ToolRegistry) -> None:
        self._repository = repository
        self._registry = registry

    def request(
        self,
        call: ToolCall,
        context: PolicyContext,
        now_utc: datetime,
    ) -> ConfirmationTicket:
        try:
            definition, _executor = self._registry.get(call.name)
        except UnknownToolError as error:
            raise ConfirmationError("unknown_tool") from error

        decision = ToolPolicy.authorize(definition, call, context)
        if decision.action == "deny":
            raise ConfirmationError(decision.code)
        if decision.action != "confirm" or definition.permission is PermissionLevel.L0:
            raise ConfirmationError("confirmation_not_required")

        try:
            validated_call = ToolCall.from_untrusted(definition, call.call_id, call.arguments)
        except ValidationError as error:
            raise ConfirmationError("invalid_arguments") from error

        digest = arguments_sha256(validated_call.arguments)
        record = self._repository.create_request(
            context.session_id,
            context.turn_id,
            validated_call,
            definition.permission,
            digest,
            now_utc,
        )
        return self._repository.create_confirmation(record.id, digest, now_utc)

    def approve(
        self,
        confirmation_id: str,
        session_id: str,
        turn_id: int,
        now_utc: datetime,
    ) -> ToolCall:
        try:
            ticket, record = self._repository.get_confirmation_request(confirmation_id)
        except KeyError as error:
            raise ConfirmationError("confirmation_unavailable") from error

        if ticket.tool_request_id != record.id:
            raise ConfirmationError("confirmation_tampered")
        if (
            record.arguments_sha256 != ticket.arguments_sha256
            or arguments_sha256(record.arguments) != record.arguments_sha256
        ):
            raise ConfirmationError("confirmation_tampered")

        try:
            definition, _executor = self._registry.get(record.tool_name)
        except UnknownToolError as error:
            raise ConfirmationError("confirmation_tampered") from error
        if record.permission != definition.permission:
            raise ConfirmationError("confirmation_tampered")

        try:
            validated_call = ToolCall.from_untrusted(
                definition,
                record.call_id,
                record.arguments,
            )
        except ValidationError as error:
            raise ConfirmationError("confirmation_tampered") from error
        if arguments_sha256(validated_call.arguments) != record.arguments_sha256:
            raise ConfirmationError("confirmation_tampered")

        consumed = self._repository.consume_confirmation(
            confirmation_id,
            record.arguments_sha256,
            session_id,
            turn_id,
            now_utc,
        )
        if not consumed:
            raise ConfirmationError("confirmation_unavailable")
        return validated_call

    def deny(
        self,
        confirmation_id: str,
        session_id: str,
        turn_id: int,
        now_utc: datetime,
    ) -> None:
        try:
            ticket, record = self._repository.get_confirmation_request(confirmation_id)
        except KeyError as error:
            raise ConfirmationError("confirmation_unavailable") from error
        if ticket.arguments_sha256 != record.arguments_sha256:
            raise ConfirmationError("confirmation_tampered")
        if not self._repository.deny_confirmation(
            confirmation_id,
            session_id,
            turn_id,
            now_utc,
        ):
            raise ConfirmationError("confirmation_unavailable")
