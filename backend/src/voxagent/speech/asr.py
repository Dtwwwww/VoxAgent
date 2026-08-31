from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Protocol

import numpy as np

from voxagent.speech.vad import (
    FRAME_SAMPLES,
    SAMPLE_RATE,
    ModelAssetError,
    prepare_sherpa_onnx_runtime,
)

SENSEVOICE_MODEL_ID = "sensevoice-int8"
PARAFORMER_MODEL_ID = "streaming-paraformer-bilingual-zh-en"
PARTIAL_UPDATE_INTERVAL_SECONDS = 0.5

_LANGUAGE_TAGS = frozenset({"zh", "en", "ja", "ko", "yue", "auto"})
_EMOTION_TAGS = frozenset({"HAPPY", "SAD", "ANGRY", "NEUTRAL", "UNKNOWN"})
_SOUND_EVENT_TAGS = frozenset(
    {
        "Speech",
        "BGM",
        "Music",
        "Applause",
        "Laughter",
        "Crying",
        "Sneeze",
        "Cough",
        "Breath",
        "Noise",
    }
)
_SENSEVOICE_INTERNAL_TAGS = frozenset({"withitn", "woitn", "nospeech"})
_CONTROL_TAG = re.compile(r"<\|([^|]+)\|>")


@dataclass(frozen=True, slots=True)
class AsrResult:
    text: str
    language: str | None = None
    emotion: str | None = None
    sound_event: str | None = None


@dataclass(frozen=True, slots=True)
class PartialAsrResult:
    text: str
    updated: bool


class AsrEngine(Protocol):
    model_id: str

    def transcribe(self, samples: np.ndarray, sample_rate: int) -> AsrResult: ...


class PartialAsrEngine(Protocol):
    model_id: str

    def accept(self, samples: np.ndarray) -> PartialAsrResult: ...

    def reset(self) -> None: ...


def _require_float32_mono(
    samples: np.ndarray,
    sample_rate: int,
    *,
    frame_only: bool = False,
) -> None:
    if sample_rate != SAMPLE_RATE:
        raise ValueError(f"ASR requires {SAMPLE_RATE} Hz audio")
    if not isinstance(samples, np.ndarray) or samples.dtype != np.float32:
        raise ValueError("ASR samples must be float32")
    if samples.ndim != 1:
        raise ValueError("ASR samples must be one-dimensional mono audio")
    if samples.size == 0:
        raise ValueError("ASR samples must be non-empty")
    if frame_only and samples.shape != (FRAME_SAMPLES,):
        raise ValueError(f"Streaming ASR requires exactly {FRAME_SAMPLES} new samples per frame")


def _required_paths(model_dir: Path, filenames: tuple[str, ...]) -> tuple[Path, ...]:
    if not model_dir.is_absolute():
        raise ValueError("ASR model directory must be absolute")
    required = tuple((model_dir / filename).resolve() for filename in filenames)
    missing = [path for path in required if not path.is_file()]
    if missing:
        raise ModelAssetError(
            "Missing ASR model assets: " + ", ".join(str(path) for path in missing)
        )
    return required


def _normalize_sensevoice_text(raw_text: str) -> AsrResult:
    language: str | None = None
    emotion: str | None = None
    sound_event: str | None = None

    def replace(match: re.Match[str]) -> str:
        nonlocal language, emotion, sound_event
        tag = match.group(1)
        if tag in _LANGUAGE_TAGS:
            language = tag
            return ""
        if tag in _EMOTION_TAGS:
            emotion = tag
            return ""
        if tag in _SOUND_EVENT_TAGS:
            sound_event = tag
            return ""
        if tag in _SENSEVOICE_INTERNAL_TAGS:
            return ""
        return match.group(0)

    return AsrResult(
        text=_CONTROL_TAG.sub(replace, raw_text).strip(),
        language=language,
        emotion=emotion,
        sound_event=sound_event,
    )


class SenseVoiceAsr:
    model_id = SENSEVOICE_MODEL_ID

    def __init__(self, recognizer: object) -> None:
        self._recognizer = recognizer

    @classmethod
    def from_model_dir(cls, path: Path, threads: int = 4) -> SenseVoiceAsr:
        model, tokens = _required_paths(Path(path), ("model.int8.onnx", "tokens.txt"))
        prepare_sherpa_onnx_runtime()
        import sherpa_onnx

        recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model),
            tokens=str(tokens),
            num_threads=threads,
            sample_rate=SAMPLE_RATE,
            provider="cpu",
            use_itn=True,
            debug=False,
        )
        return cls(recognizer)

    def transcribe(self, samples: np.ndarray, sample_rate: int) -> AsrResult:
        _require_float32_mono(samples, sample_rate)
        stream = self._recognizer.create_stream()
        stream.accept_waveform(sample_rate, samples)
        self._recognizer.decode_stream(stream)
        return _normalize_sensevoice_text(str(stream.result.text))


class SenseVoiceCandidatePauseAsr:
    """Run final SenseVoice only at benchmarked candidate pause boundaries."""

    model_id = SENSEVOICE_MODEL_ID

    def __init__(self, final_asr: AsrEngine, *, pause_interval_frames: int = 25) -> None:
        if pause_interval_frames < 1:
            raise ValueError("pause_interval_frames must be positive")
        self._final_asr = final_asr
        self._pause_interval_frames = pause_interval_frames
        self.reset()

    def reset(self) -> None:
        self._frames: list[np.ndarray] = []

    def accept(self, samples: np.ndarray) -> PartialAsrResult:
        _require_float32_mono(samples, SAMPLE_RATE, frame_only=True)
        self._frames.append(samples.copy())
        if len(self._frames) % self._pause_interval_frames:
            return PartialAsrResult(text="", updated=False)
        result = self._final_asr.transcribe(np.concatenate(self._frames), SAMPLE_RATE)
        return PartialAsrResult(text=result.text, updated=True)


class StreamingParaformerAsr:
    model_id = PARAFORMER_MODEL_ID

    def __init__(self, recognizer: object, *, clock: Callable[[], float] = monotonic) -> None:
        self._recognizer = recognizer
        self._clock = clock
        self._stream = recognizer.create_stream()
        self._latest_text = ""
        self._published_text = ""
        self._last_published_at: float | None = None

    @classmethod
    def from_model_dir(cls, path: Path, threads: int = 1) -> StreamingParaformerAsr:
        encoder, decoder, tokens = _required_paths(
            Path(path), ("encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt")
        )
        prepare_sherpa_onnx_runtime()
        import sherpa_onnx

        recognizer = sherpa_onnx.OnlineRecognizer.from_paraformer(
            encoder=str(encoder),
            decoder=str(decoder),
            tokens=str(tokens),
            num_threads=threads,
            sample_rate=SAMPLE_RATE,
            provider="cpu",
            decoding_method="greedy_search",
            enable_endpoint_detection=False,
            debug=False,
        )
        return cls(recognizer)

    def reset(self) -> None:
        self._stream = self._recognizer.create_stream()
        self._latest_text = ""
        self._published_text = ""
        self._last_published_at = None

    def accept(self, samples: np.ndarray) -> PartialAsrResult:
        _require_float32_mono(samples, SAMPLE_RATE, frame_only=True)
        self._stream.accept_waveform(SAMPLE_RATE, samples)
        while self._recognizer.is_ready(self._stream):
            self._recognizer.decode_stream(self._stream)
        self._latest_text = str(self._recognizer.get_result(self._stream).text).strip()
        now = self._clock()
        is_due = (
            self._last_published_at is None
            or now - self._last_published_at >= PARTIAL_UPDATE_INTERVAL_SECONDS
        )
        if is_due:
            self._published_text = self._latest_text
            self._last_published_at = now
            return PartialAsrResult(text=self._published_text, updated=True)
        return PartialAsrResult(text=self._published_text, updated=False)
