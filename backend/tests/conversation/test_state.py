import pytest

from voxagent.conversation.state import Phase, StaleTurnError, TurnState


def test_barge_in_cancels_reply_and_advances_turn():
    state = TurnState()
    first = state.begin_user_speech()
    state.finish_user_speech(first)
    state.begin_reply(first)
    cancelled = state.begin_user_speech()
    assert cancelled.turn_id != first.turn_id
    assert first.cancelled.is_set()
    assert state.phase is Phase.LISTENING


def test_stale_turn_cannot_enter_speaking():
    state = TurnState()
    old = state.begin_user_speech()
    state.begin_user_speech()
    with pytest.raises(StaleTurnError):
        state.begin_speaking(old)


def test_text_submission_cancels_active_reply_and_starts_thinking():
    state = TurnState()
    voice_turn = state.begin_user_speech()
    state.finish_user_speech(voice_turn)
    state.begin_reply(voice_turn)
    text_turn = state.begin_text_turn()
    assert voice_turn.cancelled.is_set()
    assert text_turn.turn_id == voice_turn.turn_id + 1
    assert state.phase is Phase.THINKING


def test_completed_turn_becomes_idle_without_marking_token_cancelled():
    state = TurnState()
    token = state.begin_text_turn()

    state.complete_turn(token)

    assert state.active_turn is None
    assert state.phase is Phase.IDLE
    assert token.cancelled.is_set() is False
