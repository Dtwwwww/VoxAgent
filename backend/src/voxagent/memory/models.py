from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal


class MemoryKind(StrEnum):
    PREFERENCE = "preference"
    PROFILE = "profile"
    HABIT = "habit"
    RELATIONSHIP = "relationship"
    EVENT = "event"


class PolicyStatus(StrEnum):
    ALLOW = "allow"
    REJECT = "reject"
    REQUIRES_CONFIRMATION = "requires_confirmation"


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    kind: MemoryKind
    content: str
    importance: float
    source_message_id: int | None
    user_explicit: bool = False
    source_role: Literal["user", "assistant"] = "user"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    status: PolicyStatus
    rule: str


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: int
    kind: MemoryKind
    content: str
    normalized_content: str
    importance: float
    source_message_id: int | None
    embedding: bytes | None
    embedding_dim: int | None
    created_at_utc: datetime
    updated_at_utc: datetime


@dataclass(frozen=True, slots=True)
class DuplicateMatch:
    memory: MemoryRecord
    similarity: float
