from pathlib import Path

import numpy as np
import pytest

from voxagent.speech.vad import ModelAssetError, VadDecision, VadDetector


class FakeVadEngine:
    def __init__(self, speech: list[bool]) -> None:
        self.speech = iter(speech)
        self.frames: list[np.ndarray] = []

    def is_speech(self, samples: np.ndarray) -> bool:
        self.frames.append(samples.copy())
        return next(self.speech)


def test_vad_requires_two_hundred_ms_of_voiced_audio_before_starting():
    engine = FakeVadEngine([True] * 11)
    detector = VadDetector(engine=engine)
    frame = b"\x00\x00" * 320

    assert [detector.accept(frame) for _ in range(9)] == [VadDecision.SILENCE] * 9
    assert detector.accept(frame) is VadDecision.STARTED
    assert detector.accept(frame) is VadDecision.SPEECH


def test_vad_converts_valid_pcm16_frame_and_reports_stop_once():
    engine = FakeVadEngine([True] * 10 + [False, False])
    detector = VadDetector(engine=engine)
    frame = np.array([-32768, 0, 32767] + [0] * 317, dtype="<i2").tobytes()

    for _ in range(9):
        detector.accept(frame)
    assert detector.accept(frame) is VadDecision.STARTED
    assert detector.accept(frame) is VadDecision.STOPPED
    assert detector.accept(frame) is VadDecision.SILENCE

    samples = engine.frames[0]
    assert samples.dtype == np.float32
    assert samples.shape == (320,)
    assert samples[0] == -1.0
    assert samples[1] == 0.0
    assert samples[2] == pytest.approx(32767 / 32768)


def test_vad_reuses_fixed_audio_frame_validation():
    detector = VadDetector(engine=FakeVadEngine([False]))

    with pytest.raises(ValueError, match="exactly 640 bytes"):
        detector.accept(b"\x00" * 639)


def test_from_model_path_rejects_missing_absolute_asset_with_its_path(tmp_path):
    model_path = (tmp_path / "silero_vad.onnx").resolve()

    with pytest.raises(ModelAssetError, match=str(model_path).replace("\\", "\\\\")):
        VadDetector.from_model_path(model_path)


def test_from_model_path_requires_an_absolute_path(tmp_path):
    relative_path = Path("silero_vad.onnx")

    with pytest.raises(ValueError, match="absolute"):
        VadDetector.from_model_path(relative_path)
