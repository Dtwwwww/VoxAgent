from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID, uuid4


class Phase(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    STOPPED = "stopped"


class StaleTurnError(RuntimeError):
    """Raised when work tries to advance a superseded conversation turn."""


@dataclass(frozen=True, slots=True)
class TurnToken:
    session_id: UUID
    turn_id: int
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)


class TurnState:
    def __init__(self, session_id: UUID | None = None) -> None:
        self.session_id = session_id or uuid4()
        self.phase = Phase.IDLE
        self._active: TurnToken | None = None
        self._next_turn_id = 0

    @property
    def active_turn(self) -> TurnToken | None:
        return self._active

    def begin_user_speech(self) -> TurnToken:
        token = self._begin_turn()
        self.phase = Phase.LISTENING
        return token

    def begin_text_turn(self) -> TurnToken:
        token = self._begin_turn()
        self.phase = Phase.THINKING
        return token

    def finish_user_speech(self, token: TurnToken) -> None:
        self._require_active(token)
        self.phase = Phase.TRANSCRIBING

    def begin_reply(self, token: TurnToken) -> None:
        self._require_active(token)
        self.phase = Phase.THINKING

    def begin_speaking(self, token: TurnToken) -> None:
        self._require_active(token)
        self.phase = Phase.SPEAKING

    def cancel_active_turn(self) -> None:
        if self._active is not None:
            self._active.cancelled.set()
            self._active = None
        self.phase = Phase.IDLE

    def complete_turn(self, token: TurnToken) -> None:
        self._require_active(token)
        self._active = None
        self.phase = Phase.IDLE

    def stop(self) -> None:
        self.cancel_active_turn()
        self.phase = Phase.STOPPED

    def _begin_turn(self) -> TurnToken:
        self.cancel_active_turn()
        self._next_turn_id += 1
        token = TurnToken(session_id=self.session_id, turn_id=self._next_turn_id)
        self._active = token
        return token

    def _require_active(self, token: TurnToken) -> None:
        active = self._active
        if active is None or token.session_id != self.session_id or token.turn_id != active.turn_id:
            raise StaleTurnError("turn token is no longer active")
