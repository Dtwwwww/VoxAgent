import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from voxagent.api.protocol import parse_client_message, parse_server_message

FIXTURES_PATH = Path(__file__).resolve().parents[3] / "contracts" / "protocol-fixtures.json"


def test_protocol_fixtures_match_contract():
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))

    client_messages = [parse_client_message(payload) for payload in fixtures["valid_client"]]
    server_messages = [parse_server_message(payload) for payload in fixtures["valid_server"]]

    assert client_messages[1].text == "hello"
    assert len(server_messages) == 12
    for payload in fixtures["invalid_client"]:
        with pytest.raises(ValidationError):
            parse_client_message(payload)
    for payload in fixtures["invalid_server"]:
        with pytest.raises(ValidationError):
            parse_server_message(payload)
