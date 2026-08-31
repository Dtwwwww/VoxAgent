from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from voxagent.conversation.events import ClientEvent, ServerEvent

_client_adapter = TypeAdapter(ClientEvent)
_server_adapter = TypeAdapter(ServerEvent)


def parse_client_message(payload: Any) -> ClientEvent:
    """Validate one JSON client event with the protocol discriminator."""
    return _client_adapter.validate_python(payload)


def parse_server_message(payload: Any) -> ServerEvent:
    """Validate one JSON server event with the protocol discriminator."""
    return _server_adapter.validate_python(payload)
