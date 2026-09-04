from __future__ import annotations

import re
import unicodedata

from voxagent.memory.models import (
    MemoryCandidate,
    MemoryKind,
    PolicyDecision,
    PolicyStatus,
)

_WHITESPACE = re.compile(r"\s+")
_PRIVATE_KEY = re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----", re.IGNORECASE)
_API_SECRET = re.compile(
    r"(?:\b(?:sk|ghp|xoxb)[-_][A-Za-z0-9_-]{8,}\b|\bgithub_pat_[A-Za-z0-9_]{8,}\b)",
    re.IGNORECASE,
)
_PASSWORD = re.compile(r"(?:密码|口令|password)\s*(?:是|[:：=])\s*\S+", re.IGNORECASE)
_GOVERNMENT_ID = re.compile(r"(?<!\d)(?:\d{17}[0-9Xx]|\d{15})(?!\d)")
_PAYMENT_DATA = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_HEALTH_DIAGNOSIS = re.compile(r"(?:医生|医院)?.{0,8}(?:确诊|诊断为|患有).{1,30}")
_ONE_TIME_LOGISTICS = re.compile(
    r"(?:今天|明天|后天|今晚|下午|上午|\d{1,2}[点时])"
    r".{0,20}(?:快递|外卖|取餐|航班|高铁|火车|会议|预约)"
)
_STABLE_KINDS = {
    MemoryKind.PREFERENCE,
    MemoryKind.PROFILE,
    MemoryKind.HABIT,
    MemoryKind.RELATIONSHIP,
}


def normalize_memory_text(content: str) -> str:
    normalized = unicodedata.normalize("NFKC", content)
    return _WHITESPACE.sub(" ", normalized).strip()


class MemoryPolicy:
    """Deterministic first-pass policy for durable-memory candidates."""

    def evaluate(self, candidate: MemoryCandidate) -> PolicyDecision:
        content = normalize_memory_text(candidate.content)
        if not content:
            return PolicyDecision(PolicyStatus.REJECT, "empty_content")
        if len(content) > 500:
            return PolicyDecision(PolicyStatus.REJECT, "content_too_long")

        for pattern, rule in (
            (_PRIVATE_KEY, "private_key"),
            (_PASSWORD, "password_secret"),
            (_API_SECRET, "api_secret"),
            (_GOVERNMENT_ID, "government_id"),
            (_PAYMENT_DATA, "payment_data"),
        ):
            if pattern.search(content):
                return PolicyDecision(PolicyStatus.REJECT, rule)

        if candidate.source_role != "user":
            return PolicyDecision(PolicyStatus.REJECT, "assistant_generated")
        if _ONE_TIME_LOGISTICS.search(content):
            return PolicyDecision(PolicyStatus.REJECT, "one_time_logistics")
        if _HEALTH_DIAGNOSIS.search(content):
            status = (
                PolicyStatus.REQUIRES_CONFIRMATION
                if candidate.user_explicit
                else PolicyStatus.REJECT
            )
            return PolicyDecision(status, "health_diagnosis")
        if candidate.kind in _STABLE_KINDS:
            return PolicyDecision(PolicyStatus.ALLOW, "stable_user_memory")
        if candidate.kind is MemoryKind.EVENT and candidate.user_explicit:
            return PolicyDecision(PolicyStatus.ALLOW, "explicit_user_memory")
        return PolicyDecision(PolicyStatus.REJECT, "non_durable_event")
