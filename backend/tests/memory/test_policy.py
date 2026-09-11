from __future__ import annotations

import pytest

from voxagent.memory.models import MemoryCandidate, MemoryKind, PolicyStatus
from voxagent.memory.policy import MemoryPolicy, normalize_memory_text


@pytest.mark.parametrize(
    ("kind", "content"),
    [
        (MemoryKind.PREFERENCE, "我喜欢喝无糖咖啡"),
        (MemoryKind.PROFILE, "我在杭州工作"),
        (MemoryKind.HABIT, "我每周三晚上跑步"),
        (MemoryKind.RELATIONSHIP, "回答时请称呼我为小林"),
    ],
)
def test_allows_stable_user_memory(kind: MemoryKind, content: str) -> None:
    decision = MemoryPolicy().evaluate(
        MemoryCandidate(kind, content, 0.8, source_message_id=1)
    )

    assert decision.status is PolicyStatus.ALLOW
    assert decision.rule == "stable_user_memory"


@pytest.mark.parametrize(
    ("candidate", "rule"),
    [
        (
            MemoryCandidate(MemoryKind.EVENT, "我今天下午三点取快递", 0.4, 1),
            "one_time_logistics",
        ),
        (
            MemoryCandidate(
                MemoryKind.PROFILE,
                "用户可能喜欢爵士乐",
                0.4,
                1,
                source_role="assistant",
            ),
            "assistant_generated",
        ),
        (
            MemoryCandidate(MemoryKind.PROFILE, "我的 API token 是 sk-secret123456", 1, 1),
            "api_secret",
        ),
        (
            MemoryCandidate(MemoryKind.PROFILE, "Authorization Bearer abcDEF123456", 1, 1),
            "api_secret",
        ),
        (
            MemoryCandidate(MemoryKind.PROFILE, "我的密码 abc123456", 1, 1),
            "password_secret",
        ),
        (
            MemoryCandidate(
                MemoryKind.PROFILE,
                "-----BEGIN PRIVATE KEY----- abc",
                1,
                1,
            ),
            "private_key",
        ),
        (
            MemoryCandidate(MemoryKind.PROFILE, "银行卡号 6222 0200 1234 5678", 1, 1),
            "payment_data",
        ),
        (
            MemoryCandidate(MemoryKind.PROFILE, "身份证 11010519491231002X", 1, 1),
            "government_id",
        ),
        (
            MemoryCandidate(MemoryKind.PROFILE, "医生确诊我有高血压", 1, 1),
            "health_diagnosis",
        ),
        (
            MemoryCandidate(MemoryKind.PROFILE, "很长" * 251, 1, 1),
            "content_too_long",
        ),
    ],
)
def test_rejects_unsafe_or_non_durable_memory(
    candidate: MemoryCandidate, rule: str
) -> None:
    decision = MemoryPolicy().evaluate(candidate)

    assert decision.status is PolicyStatus.REJECT
    assert decision.rule == rule


def test_explicit_sensitive_non_secret_fact_requires_confirmation() -> None:
    candidate = MemoryCandidate(
        MemoryKind.PROFILE,
        "医生确诊我有高血压",
        0.9,
        source_message_id=2,
        user_explicit=True,
    )

    decision = MemoryPolicy().evaluate(candidate)

    assert decision.status is PolicyStatus.REQUIRES_CONFIRMATION
    assert decision.rule == "health_diagnosis"


def test_explicit_secret_is_still_rejected() -> None:
    candidate = MemoryCandidate(
        MemoryKind.PROFILE,
        "请记住我的密码是 abc123456",
        1,
        source_message_id=3,
        user_explicit=True,
    )

    assert MemoryPolicy().evaluate(candidate).status is PolicyStatus.REJECT


def test_normalization_folds_full_width_and_whitespace() -> None:
    assert normalize_memory_text("  我喜欢ＡＩ\n 助手  ") == "我喜欢AI 助手"
