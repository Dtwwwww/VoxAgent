import sys
from types import SimpleNamespace

import numpy as np
import pytest

from voxagent.speech.asr import (
    AsrResult,
    SenseVoiceAsr,
    SenseVoiceCandidatePauseAsr,
    StreamingParaformerAsr,
)
from voxagent.speech.vad import ModelAssetError


class FakeOfflineStream:
    def __init__(self, result: SimpleNamespace) -> None:
        self.result = result
        self.accepted: list[tuple[int, np.ndarray]] = []

    def accept_waveform(self, sample_rate: int, samples: np.ndarray) -> None:
        self.accepted.append((sample_rate, samples.copy()))


class FakeOfflineRecognizer:
    def __init__(self, result: SimpleNamespace) -> None:
        self.stream = FakeOfflineStream(result)
        self.decoded: list[FakeOfflineStream] = []

    def create_stream(self) -> FakeOfflineStream:
        return self.stream

    def decode_stream(self, stream: FakeOfflineStream) -> None:
        self.decoded.append(stream)


def test_sensevoice_transcribes_float32_mono_and_preserves_metadata():
    recognizer = FakeOfflineRecognizer(
        SimpleNamespace(text="<|zh|><|HAPPY|><|Speech|>你好，<div>世界</div>！")
    )
    engine = SenseVoiceAsr(recognizer)

    result = engine.transcribe(np.array([0.0, 0.25], dtype=np.float32), sample_rate=16000)

    assert result == AsrResult(
        text="你好，<div>世界</div>！",
        language="zh",
        emotion="HAPPY",
        sound_event="Speech",
    )
    assert recognizer.stream.accepted[0][0] == 16000
    assert recognizer.stream.accepted[0][1].dtype == np.float32


@pytest.mark.parametrize(
    ("samples", "sample_rate", "message"),
    [
        (np.array([], dtype=np.float32), 16000, "non-empty"),
        (np.zeros((1, 2), dtype=np.float32), 16000, "one-dimensional"),
        (np.zeros(2, dtype=np.float64), 16000, "float32"),
        (np.zeros(2, dtype=np.float32), 8000, "16000"),
    ],
)
def test_sensevoice_rejects_invalid_pcm(samples: np.ndarray, sample_rate: int, message: str):
    engine = SenseVoiceAsr(FakeOfflineRecognizer(SimpleNamespace(text="你好")))

    with pytest.raises(ValueError, match=message):
        engine.transcribe(samples, sample_rate)


def test_sensevoice_model_factory_uses_cpu_configuration_and_shared_runtime_loader(
    monkeypatch, tmp_path
):
    from voxagent.speech import asr

    model_dir = tmp_path.resolve()
    (model_dir / "model.int8.onnx").touch()
    (model_dir / "tokens.txt").touch()
    calls: dict[str, object] = {}

    class FakeOfflineRecognizerModule:
        @staticmethod
        def from_sense_voice(**kwargs):
            calls.update(kwargs)
            return FakeOfflineRecognizer(SimpleNamespace(text="<|en|>hello"))

    monkeypatch.setattr(
        asr,
        "prepare_sherpa_onnx_runtime",
        lambda: calls.setdefault("runtime", True),
    )
    monkeypatch.setitem(
        sys.modules,
        "sherpa_onnx",
        SimpleNamespace(OfflineRecognizer=FakeOfflineRecognizerModule),
    )

    engine = SenseVoiceAsr.from_model_dir(model_dir)

    assert isinstance(engine, SenseVoiceAsr)
    assert calls == {
        "runtime": True,
        "model": str(model_dir / "model.int8.onnx"),
        "tokens": str(model_dir / "tokens.txt"),
        "num_threads": 4,
        "sample_rate": 16000,
        "provider": "cpu",
        "use_itn": True,
        "debug": False,
    }


def test_sensevoice_model_factory_lists_missing_absolute_assets(tmp_path):
    model_dir = tmp_path.resolve()

    with pytest.raises(ModelAssetError) as raised:
        SenseVoiceAsr.from_model_dir(model_dir)

    assert str(model_dir / "model.int8.onnx") in str(raised.value)
    assert str(model_dir / "tokens.txt") in str(raised.value)


class FakeOnlineStream:
    def __init__(self) -> None:
        self.accepted: list[tuple[int, np.ndarray]] = []

    def accept_waveform(self, sample_rate: int, samples: np.ndarray) -> None:
        self.accepted.append((sample_rate, samples.copy()))


class FakeOnlineRecognizer:
    def __init__(self, texts: list[str]) -> None:
        self.stream = FakeOnlineStream()
        self.texts = iter(texts)
        self.decode_calls = 0

    def create_stream(self) -> FakeOnlineStream:
        return self.stream

    def is_ready(self, stream: FakeOnlineStream) -> bool:
        return self.decode_calls < len(self.stream.accepted)

    def decode_stream(self, stream: FakeOnlineStream) -> None:
        self.decode_calls += 1

    def get_result(self, stream: FakeOnlineStream) -> str:
        return next(self.texts)


def test_streaming_paraformer_uses_only_new_frames_and_throttles_public_updates():
    clock = iter([0.0, 0.2, 0.5])
    recognizer = FakeOnlineRecognizer(["你", "你好", "你好啊"])
    engine = StreamingParaformerAsr(recognizer, clock=lambda: next(clock))
    frame = np.zeros(320, dtype=np.float32)

    first = engine.accept(frame)
    throttled = engine.accept(frame)
    published = engine.accept(frame)

    assert first.text == "你"
    assert first.updated is True
    assert throttled.text == "你"
    assert throttled.updated is False
    assert published.text == "你好啊"
    assert published.updated is True
    assert len(recognizer.stream.accepted) == 3
    assert all(samples.shape == (320,) for _, samples in recognizer.stream.accepted)


def test_streaming_paraformer_factory_disables_builtin_endpointing(monkeypatch, tmp_path):
    from voxagent.speech import asr

    model_dir = tmp_path.resolve()
    for name in ("encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt"):
        (model_dir / name).touch()
    calls: dict[str, object] = {}

    class FakeOnlineRecognizerModule:
        @staticmethod
        def from_paraformer(**kwargs):
            calls.update(kwargs)
            return FakeOnlineRecognizer([""])

    monkeypatch.setattr(
        asr,
        "prepare_sherpa_onnx_runtime",
        lambda: calls.setdefault("runtime", True),
    )
    monkeypatch.setitem(
        sys.modules,
        "sherpa_onnx",
        SimpleNamespace(OnlineRecognizer=FakeOnlineRecognizerModule),
    )

    StreamingParaformerAsr.from_model_dir(model_dir)

    assert calls == {
        "runtime": True,
        "encoder": str(model_dir / "encoder.int8.onnx"),
        "decoder": str(model_dir / "decoder.int8.onnx"),
        "tokens": str(model_dir / "tokens.txt"),
        "num_threads": 1,
        "sample_rate": 16000,
        "provider": "cpu",
        "decoding_method": "greedy_search",
        "enable_endpoint_detection": False,
        "debug": False,
    }


def test_streaming_paraformer_rejects_non_frame_input():
    engine = StreamingParaformerAsr(FakeOnlineRecognizer([""]))

    with pytest.raises(ValueError, match="320"):
        engine.accept(np.zeros(319, dtype=np.float32))


def test_sensevoice_candidate_pause_adapter_only_decodes_at_candidate_pauses():
    final = FakeOfflineRecognizer(SimpleNamespace(text="<|zh|>打开音乐"))
    engine = SenseVoiceCandidatePauseAsr(SenseVoiceAsr(final), pause_interval_frames=2)
    frame = np.zeros(320, dtype=np.float32)

    pending = engine.accept(frame)
    update = engine.accept(frame)

    assert pending.updated is False
    assert update.text == "打开音乐"
    assert update.updated is True
    assert len(final.stream.accepted) == 1
    assert final.stream.accepted[0][1].shape == (640,)
