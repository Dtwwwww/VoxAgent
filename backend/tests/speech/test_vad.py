import os
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from voxagent.speech.vad import (
    MIN_SPEECH_DURATION_SECONDS,
    SILERO_SPEECH_THRESHOLD,
    ModelAssetError,
    SherpaVadEngine,
    VadDecision,
    VadDetector,
)


class FakeVadEngine:
    def __init__(self, speech: list[bool]) -> None:
        self.speech = iter(speech)
        self.frames: list[np.ndarray] = []

    def is_speech_detected(self, samples: np.ndarray) -> bool:
        self.frames.append(samples.copy())
        return next(self.speech)


class ThresholdFakeVadEngine:
    """Models an engine whose own configured 200 ms gate emits the first True."""

    def __init__(self, start_after_frames: int) -> None:
        self.start_after_frames = start_after_frames
        self.frames: list[np.ndarray] = []

    def is_speech_detected(self, samples: np.ndarray) -> bool:
        self.frames.append(samples.copy())
        return len(self.frames) >= self.start_after_frames


class FakeSherpaVadModule:
    def __init__(self) -> None:
        self.silero_config: object | None = None
        self.vad_config: object | None = None

    class SileroVadModelConfig:
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    class VadModelConfig:
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    class VoiceActivityDetector:
        def __init__(self, config) -> None:
            self.config = config
            self.accepted: list[np.ndarray] = []

        def accept_waveform(self, samples: np.ndarray) -> None:
            self.accepted.append(samples)

        def is_speech_detected(self) -> bool:
            return bool(self.accepted)


def test_vad_starts_on_the_engine_two_hundred_ms_speech_transition():
    engine = ThresholdFakeVadEngine(start_after_frames=10)
    detector = VadDetector(engine=engine)
    frame = b"\x00\x00" * 320

    assert [detector.accept(frame) for _ in range(9)] == [VadDecision.SILENCE] * 9
    assert detector.accept(frame) is VadDecision.STARTED
    assert detector.accept(frame) is VadDecision.SPEECH


def test_vad_converts_valid_pcm16_frame_and_reports_stop_once():
    engine = FakeVadEngine([False] * 9 + [True, False, False])
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


def test_sherpa_vad_engine_configures_pinned_cpu_parameters_without_loading_weights(
    monkeypatch, tmp_path
):
    fake_module = FakeSherpaVadModule()
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake_module)
    model_path = (tmp_path / "silero_vad.onnx").resolve()

    engine = SherpaVadEngine(model_path, sample_rate=16000)

    config = engine._detector.config
    assert config.sample_rate == 16000
    assert config.num_threads == 1
    assert config.provider == "cpu"
    assert config.debug is False
    assert config.silero_vad.model == str(model_path)
    assert SILERO_SPEECH_THRESHOLD == 0.35
    assert MIN_SPEECH_DURATION_SECONDS == 0.1
    assert config.silero_vad.threshold == SILERO_SPEECH_THRESHOLD
    assert config.silero_vad.min_speech_duration == MIN_SPEECH_DURATION_SECONDS


def test_sherpa_vad_engine_uses_the_bundled_onnxruntime_directory_before_import(
    monkeypatch, tmp_path
):
    from voxagent.speech import vad

    fake_module = FakeSherpaVadModule()
    onnxruntime_package = tmp_path / "onnxruntime"
    runtime_directory = onnxruntime_package / "capi"
    runtime_directory.mkdir(parents=True)
    (runtime_directory / "onnxruntime.dll").touch()
    runtime_init = onnxruntime_package / "__init__.py"
    runtime_init.touch()
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        types.SimpleNamespace(__file__=str(runtime_init), __version__="1.27.0"),
    )
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake_module)
    added_directories: list[str] = []
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    monkeypatch.setattr(os, "add_dll_directory", lambda path: added_directories.append(path))
    monkeypatch.setattr(vad, "_ONNXRUNTIME_DLL_DIRECTORIES", [])

    SherpaVadEngine((tmp_path / "silero_vad.onnx").resolve(), sample_rate=16000)

    assert added_directories == [str(runtime_directory)]
    assert os.environ["PATH"].split(os.pathsep)[0] == str(runtime_directory)


@pytest.mark.local_vad_smoke
def test_local_silero_vad_initialization_smoke():
    model_path = os.environ.get("VOXAGENT_LOCAL_SILERO_VAD_PATH")
    if not model_path:
        pytest.skip("set VOXAGENT_LOCAL_SILERO_VAD_PATH to enable this local integration smoke")

    command = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            "from voxagent.speech.vad import VadDetector; "
            f"VadDetector.from_model_path(Path({model_path!r})); "
            "print('VAD_SMOKE_OK')"
        ),
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "VAD_SMOKE_OK" in result.stdout


def test_from_model_path_rejects_missing_absolute_asset_with_its_path(tmp_path):
    model_path = (tmp_path / "silero_vad.onnx").resolve()

    with pytest.raises(ModelAssetError, match=str(model_path).replace("\\", "\\\\")):
        VadDetector.from_model_path(model_path)


def test_from_model_path_requires_an_absolute_path(tmp_path):
    relative_path = Path("silero_vad.onnx")

    with pytest.raises(ValueError, match="absolute"):
        VadDetector.from_model_path(relative_path)
