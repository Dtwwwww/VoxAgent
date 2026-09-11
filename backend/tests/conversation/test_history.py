import pytest

from voxagent.conversation.history import (
    SYSTEM_INSTRUCTION,
    ChatMessage,
    ConversationHistory,
)


def test_history_keeps_mixed_sources_private_and_model_messages_typed():
    history = ConversationHistory()
    history.add_user(1, "第一问", "text")
    history.complete_assistant(1, "第一答")
    history.add_user(2, "第二问", "voice")

    assert history.messages_for_model() == (
        ChatMessage("system", SYSTEM_INSTRUCTION),
        ChatMessage("user", "第一问"),
        ChatMessage("assistant", "第一答"),
        ChatMessage("user", "第二问"),
    )
    assert all(not hasattr(message, "source") for message in history.messages_for_model())


def test_history_trims_oldest_complete_pairs_without_splitting_them():
    budget = len(SYSTEM_INSTRUCTION) + len("current") + len("new-u") + len("new-a")
    history = ConversationHistory(codepoint_budget=budget)
    history.add_user(1, "old-u", "text")
    history.complete_assistant(1, "old-a")
    history.add_user(2, "new-u", "voice")
    history.complete_assistant(2, "new-a")
    history.add_user(3, "current", "text")

    assert history.messages_for_model() == (
        ChatMessage("system", SYSTEM_INSTRUCTION),
        ChatMessage("user", "new-u"),
        ChatMessage("assistant", "new-a"),
        ChatMessage("user", "current"),
    )


def test_cancelled_turn_discards_user_and_partial_assistant():
    history = ConversationHistory()
    history.add_user(1, "保留", "text")
    history.complete_assistant(1, "完成")
    history.add_user(2, "取消", "voice")
    history.cancel_turn(2)

    assert history.messages_for_model()[-2:] == (
        ChatMessage("user", "保留"),
        ChatMessage("assistant", "完成"),
    )
    with pytest.raises(KeyError):
        history.assistant_text(2)


def test_complete_assistant_rejects_stale_or_duplicate_turns():
    history = ConversationHistory()
    history.add_user(1, "问题", "text")

    with pytest.raises(ValueError, match="active"):
        history.complete_assistant(2, "错轮")
    history.complete_assistant(1, "回答")
    with pytest.raises(ValueError, match="active"):
        history.complete_assistant(1, "重复")


def test_clear_releases_all_session_messages():
    history = ConversationHistory()
    history.add_user(1, "问题", "voice")
    history.complete_assistant(1, "回答")

    history.clear()

    assert history.messages_for_model() == (ChatMessage("system", SYSTEM_INSTRUCTION),)
    with pytest.raises(KeyError):
        history.assistant_text(1)
