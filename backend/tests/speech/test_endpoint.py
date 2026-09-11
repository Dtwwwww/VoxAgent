import pytest

from voxagent.speech.endpoint import EndpointDecision, EndpointDetector
from voxagent.speech.vad import VadDecision


@pytest.mark.parametrize(
    ("profile", "silence_ms"),
    [("fast", 800), ("natural", 1350), ("patient", 2000)],
)
def test_profile_finishes_at_its_threshold(profile, silence_ms):
    detector = EndpointDetector(profile=profile)
    detector.mark_speech(now_ms=0)

    assert not detector.should_finish(now_ms=silence_ms - 1, partial_text="今天")
    assert detector.should_finish(now_ms=silence_ms, partial_text="今天")


@pytest.mark.parametrize("text", ["我觉得那个", "然后呢", "嗯", "就是", "因为"])
def test_natural_profile_extends_incomplete_utterance_to_two_seconds(text):
    detector = EndpointDetector(profile="natural")
    detector.mark_speech(now_ms=0)

    assert not detector.should_finish(now_ms=1999, partial_text=text)
    assert detector.should_finish(now_ms=2000, partial_text=text)


def test_accept_never_commits_continuous_silence_before_speech():
    detector = EndpointDetector(profile="fast")

    assert (
        detector.accept(VadDecision.SILENCE, now_ms=0, partial_text="")
        is EndpointDecision.CONTINUE
    )
    assert (
        detector.accept(VadDecision.SILENCE, now_ms=10_000, partial_text="")
        is EndpointDecision.CONTINUE
    )


def test_accept_commits_after_speech_then_profile_silence_threshold():
    detector = EndpointDetector(profile="fast")

    assert (
        detector.accept(VadDecision.STARTED, now_ms=0, partial_text="今天")
        is EndpointDecision.CONTINUE
    )
    assert (
        detector.accept(VadDecision.SILENCE, now_ms=799, partial_text="今天")
        is EndpointDecision.CONTINUE
    )
    assert (
        detector.accept(VadDecision.SILENCE, now_ms=800, partial_text="今天")
        is EndpointDecision.COMMIT
    )
