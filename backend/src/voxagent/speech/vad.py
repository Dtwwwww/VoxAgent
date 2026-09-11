from __future__ import annotations

import os
import sys
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import numpy as np

from voxagent.api.protocol import validate_audio_frame

SAMPLE_RATE = 16000
FRAME_SAMPLES = 320
SILERO_SPEECH_THRESHOLD = 0.35
MIN_SPEECH_DURATION_SECONDS = 0.1
ONNXRUNTIME_VERSION = "1.27.0"
_ONNXRUNTIME_DLL_DIRECTORIES: list[object] = []


class ModelAssetError(FileNotFoundError):
    """An offline speech model asset is absent or cannot be used."""


class VadRuntimeError(RuntimeError):
    """The pinned local VAD runtime could not be initialized."""


class VadDecision(StrEnum):
    STARTED = "started"
    SPEECH = "speech"
    STOPPED = "stopped"
    SILENCE = "silence"


class VadEngine(Protocol):
    def is_speech_detected(self, samples: np.ndarray) -> bool:
        """Accept one frame and report only after the engine's own speech gate is met."""
        ...


def prepare_sherpa_onnx_runtime() -> None:
    """Ensure sherpa resolves the pinned ONNX Runtime instead of a System32 DLL."""
    if sys.platform != "win32":
        return

    try:
        import onnxruntime
    except ImportError as error:
        raise VadRuntimeError(
            f"Windows Silero VAD requires onnxruntime=={ONNXRUNTIME_VERSION}; "
            "install the speech extra"
        ) from error

    if onnxruntime.__version__ != ONNXRUNTIME_VERSION:
        raise VadRuntimeError(
            f"Windows Silero VAD requires onnxruntime=={ONNXRUNTIME_VERSION}, "
            f"found {onnxruntime.__version__}"
        )

    runtime_directory = Path(onnxruntime.__file__).resolve().parent / "capi"
    if not (runtime_directory / "onnxruntime.dll").is_file():
        raise VadRuntimeError(f"Missing ONNX Runtime DLL: {runtime_directory / 'onnxruntime.dll'}")

    runtime_directory_text = str(runtime_directory)
    _ONNXRUNTIME_DLL_DIRECTORIES.append(os.add_dll_directory(runtime_directory_text))
    current_path = os.environ.get("PATH", "")
    if runtime_directory_text not in current_path.split(os.pathsep):
        os.environ["PATH"] = runtime_directory_text + os.pathsep + current_path


class SherpaVadEngine:
    """Small adapter that keeps sherpa-onnx out of the detector state machine."""

    def __init__(self, model_path: Path, sample_rate: int) -> None:
        prepare_sherpa_onnx_runtime()
        import sherpa_onnx

        config = sherpa_onnx.VadModelConfig(
            silero_vad=sherpa_onnx.SileroVadModelConfig(
                model=str(model_path),
                threshold=SILERO_SPEECH_THRESHOLD,
                min_speech_duration=MIN_SPEECH_DURATION_SECONDS,
            ),
            sample_rate=sample_rate,
            num_threads=1,
            provider="cpu",
            debug=False,
        )
        self._detector = sherpa_onnx.VoiceActivityDetector(config)

    def is_speech_detected(self, samples: np.ndarray) -> bool:
        self._detector.accept_waveform(samples)
        return self._detector.is_speech_detected()


class VadDetector:
    """Turn fixed 20 ms PCM16 frames into stable VAD transitions."""

    def __init__(self, engine: VadEngine, sample_rate: int = SAMPLE_RATE) -> None:
        if sample_rate != SAMPLE_RATE:
            raise ValueError(f"Silero VAD requires {SAMPLE_RATE} Hz audio")
        self._engine = engine
        self._speaking = False

    @classmethod
    def from_model_path(cls, path: Path, sample_rate: int = SAMPLE_RATE) -> VadDetector:
        model_path = Path(path)
        if not model_path.is_absolute():
            raise ValueError("Silero VAD model path must be absolute")
        if not model_path.is_file():
            raise ModelAssetError(f"Missing Silero VAD model asset: {model_path}")
        return cls(engine=SherpaVadEngine(model_path, sample_rate), sample_rate=sample_rate)

    def accept(self, frame: bytes) -> VadDecision:
        validated = validate_audio_frame(frame)
        samples = np.frombuffer(validated, dtype="<i2").astype(np.float32) / 32768.0
        if samples.shape != (FRAME_SAMPLES,):
            raise ValueError(f"audio frame must contain exactly {FRAME_SAMPLES} samples")

        if self._engine.is_speech_detected(samples):
            if not self._speaking:
                self._speaking = True
                return VadDecision.STARTED
            return VadDecision.SPEECH

        if self._speaking:
            self._speaking = False
            return VadDecision.STOPPED
        return VadDecision.SILENCE
