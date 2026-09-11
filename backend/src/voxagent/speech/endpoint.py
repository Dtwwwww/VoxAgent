from __future__ import annotations

import re
from enum import StrEnum
from types import MappingProxyType

from voxagent.speech.vad import VadDecision


class EndpointProfile(StrEnum):
    FAST = "fast"
    NATURAL = "natural"
    PATIENT = "patient"


class EndpointDecision(StrEnum):
    CONTINUE = "continue"
    COMMIT = "commit"


SILENCE_THRESHOLDS_MS = MappingProxyType(
    {
        EndpointProfile.FAST: 800,
        EndpointProfile.NATURAL: 1350,
        EndpointProfile.PATIENT: 2000,
    }
)
INCOMPLETE_ENDING = re.compile(
    r"(?:嗯+|呃+|就是|然后|然后呢|那个|我觉得|因为|所以|但是|还有|的话)$"
)


class EndpointDetector:
    """Apply the fixed Mandarin endpoint policy to VAD state transitions."""

    def __init__(self, profile: EndpointProfile | str = EndpointProfile.NATURAL) -> None:
        self.profile = EndpointProfile(profile)
        self._last_speech_ms: int | None = None

    def mark_speech(self, now_ms: int) -> None:
        self._last_speech_ms = now_ms

    def should_finish(self, now_ms: int, partial_text: str) -> bool:
        if self._last_speech_ms is None:
            return False
        return now_ms - self._last_speech_ms >= self._silence_threshold(partial_text)

    def accept(
        self,
        decision: VadDecision,
        now_ms: int,
        partial_text: str,
    ) -> EndpointDecision:
        if decision in (VadDecision.STARTED, VadDecision.SPEECH):
            self.mark_speech(now_ms)
        if self.should_finish(now_ms, partial_text):
            return EndpointDecision.COMMIT
        return EndpointDecision.CONTINUE

    def _silence_threshold(self, partial_text: str) -> int:
        normalized = "".join(partial_text.split())
        if self.profile is EndpointProfile.NATURAL and INCOMPLETE_ENDING.search(normalized):
            return SILENCE_THRESHOLDS_MS[EndpointProfile.PATIENT]
        return SILENCE_THRESHOLDS_MS[self.profile]
