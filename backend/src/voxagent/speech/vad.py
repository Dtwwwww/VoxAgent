from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Protocol

import numpy as np

from voxagent.api.protocol import validate_audio_frame

SAMPLE_RATE = 16000
FRAME_SAMPLES = 320
MIN_SPEECH_DURATION_SECONDS = 0.2
MIN_SPEECH_FRAMES = int(SAMPLE_RATE * MIN_SPEECH_DURATION_SECONDS / FRAME_SAMPLES)


class ModelAssetError(FileNotFoundError):
    """An offline speech model asset is absent or cannot be used."""


class VadDecision(StrEnum):
    STARTED = "started"
    SPEECH = "speech"
    STOPPED = "stopped"
    SILENCE = "silence"


class VadEngine(Protocol):
    def is_speech(self, samples: np.ndarray) -> bool: ...


class SherpaVadEngine:
    """Small adapter that keeps sherpa-onnx out of the detector state machine."""

    def __init__(self, model_path: Path, sample_rate: int) -> None:
        import sherpa_onnx

        config = sherpa_onnx.VadModelConfig(
            silero_vad=sherpa_onnx.SileroVadModelConfig(
                model=str(model_path),
                threshold=0.5,
                min_speech_duration=MIN_SPEECH_DURATION_SECONDS,
            ),
            sample_rate=sample_rate,
            num_threads=1,
            provider="cpu",
            debug=False,
        )
        self._detector = sherpa_onnx.VoiceActivityDetector(config)

    def is_speech(self, samples: np.ndarray) -> bool:
        self._detector.accept_waveform(samples)
        return self._detector.is_speech_detected()


class VadDetector:
    """Turn fixed 20 ms PCM16 frames into stable VAD transitions."""

    def __init__(self, engine: VadEngine, sample_rate: int = SAMPLE_RATE) -> None:
        if sample_rate != SAMPLE_RATE:
            raise ValueError(f"Silero VAD requires {SAMPLE_RATE} Hz audio")
        self._engine = engine
        self._voiced_frames = 0
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

        if self._engine.is_speech(samples):
            self._voiced_frames += 1
            if not self._speaking and self._voiced_frames >= MIN_SPEECH_FRAMES:
                self._speaking = True
                return VadDecision.STARTED
            if self._speaking:
                return VadDecision.SPEECH
            return VadDecision.SILENCE

        self._voiced_frames = 0
        if self._speaking:
            self._speaking = False
            return VadDecision.STOPPED
        return VadDecision.SILENCE
