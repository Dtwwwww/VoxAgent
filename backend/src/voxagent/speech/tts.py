from __future__ import annotations

import io
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import numpy as np
import soundfile

from voxagent.speech.vad import prepare_sherpa_onnx_runtime
from voxagent.speech.voice_catalog import VoiceCatalog

PUBLIC_TTS_SPEEDS = frozenset({0.8, 1.0, 1.2})
TtsEngineName = Literal["kokoro", "melo"]
VOICE_REVIEW_SEED = 20260830
KOKORO_REVIEW_VOICE_IDS = (3, 13, 27, 43, 58, 69, 85, 98)
VOICE_REVIEW_SENTENCE = "你好，我是声灵，很高兴陪你一起聊天。"


@dataclass(frozen=True, slots=True)
class AudioChunk:
    wav_bytes: bytes
    sample_rate: int
    duration_seconds: float

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if not self.wav_bytes:
            raise ValueError("wav_bytes must be non-empty")


class TtsEngine(Protocol):
    def synthesize(self, text: str, voice_key: str, speed: float) -> AudioChunk: ...


class _NativeSynthesizer(Protocol):
    def generate(self, text: str, sid: int, speed: float) -> object: ...


class SherpaOfflineTts:
    """CPU-only sherpa-onnx adapter which keeps speaker IDs server-side."""

    def __init__(
        self,
        synthesizer: _NativeSynthesizer,
        catalog: VoiceCatalog | None,
        *,
        engine: TtsEngineName,
    ) -> None:
        self._synthesizer = synthesizer
        self._catalog = catalog
        self.engine = engine

    @classmethod
    def from_model_dir(
        cls,
        path: Path,
        voice_id: int,
        *,
        engine: TtsEngineName,
        catalog: VoiceCatalog | None = None,
    ) -> SherpaOfflineTts:
        model_dir = Path(path).resolve()
        if not model_dir.is_absolute() or voice_id < 0:
            raise ValueError("TTS model directory must be absolute and voice_id non-negative")
        prepare_sherpa_onnx_runtime()
        import sherpa_onnx

        if engine == "kokoro":
            required = (
                "model.int8.onnx",
                "voices.bin",
                "tokens.txt",
                "lexicon-zh.txt",
                "espeak-ng-data",
            )
            cls._require_assets(model_dir, required)
            model = sherpa_onnx.OfflineTtsKokoroModelConfig(
                model=str(model_dir / "model.int8.onnx"),
                voices=str(model_dir / "voices.bin"),
                tokens=str(model_dir / "tokens.txt"),
                lexicon=str(model_dir / "lexicon-zh.txt"),
                data_dir=str(model_dir / "espeak-ng-data"),
                dict_dir=str(model_dir / "dict"),
            )
            config = sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(
                    kokoro=model, num_threads=1, provider="cpu", debug=False
                ),
                max_num_sentences=1,
            )
        elif engine == "melo":
            required = ("model.onnx", "tokens.txt", "lexicon.txt")
            cls._require_assets(model_dir, required)
            model = sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(model_dir / "model.onnx"),
                tokens=str(model_dir / "tokens.txt"),
                lexicon=str(model_dir / "lexicon.txt"),
                data_dir=str(model_dir),
            )
            config = sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(
                    vits=model, num_threads=1, provider="cpu", debug=False
                ),
                max_num_sentences=1,
            )
        else:
            raise ValueError(f"Unsupported TTS engine: {engine}")
        return cls(sherpa_onnx.OfflineTts(config), catalog, engine=engine)

    @staticmethod
    def _require_assets(model_dir: Path, names: tuple[str, ...]) -> None:
        missing = [name for name in names if not (model_dir / name).exists()]
        if missing:
            raise FileNotFoundError("Missing TTS model assets: " + ", ".join(missing))

    def synthesize(self, text: str, voice_key: str, speed: float) -> AudioChunk:
        if self._catalog is None:
            raise RuntimeError("A server-owned voice catalog is required for public synthesis")
        profile = self._catalog.get(voice_key)
        if profile.engine != self.engine:
            raise ValueError("voice_key is not available in this TTS engine")
        return self.synthesize_native(text, profile.native_voice_id, speed)

    def synthesize_native(self, text: str, native_voice_id: int, speed: float = 1.0) -> AudioChunk:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("TTS text must be non-empty")
        if speed not in PUBLIC_TTS_SPEEDS:
            raise ValueError("TTS speed must be exactly one of 0.8, 1.0, 1.2")
        if (
            isinstance(native_voice_id, bool)
            or not isinstance(native_voice_id, int)
            or native_voice_id < 0
        ):
            raise ValueError("native_voice_id must be a non-negative integer")
        generated = self._synthesizer.generate(text, sid=native_voice_id, speed=speed)
        samples = np.asarray(generated.samples, dtype=np.float32)
        if samples.ndim == 2 and samples.shape[1] == 1:
            samples = samples[:, 0]
        if samples.ndim != 1 or samples.size == 0:
            raise RuntimeError("TTS engine returned no mono samples")
        sample_rate = int(generated.sample_rate)
        if sample_rate <= 0:
            raise RuntimeError("TTS engine returned an invalid sample rate")
        stream = io.BytesIO()
        soundfile.write(
            stream,
            np.clip(samples, -1.0, 1.0),
            sample_rate,
            format="WAV",
            subtype="PCM_16",
        )
        return AudioChunk(
            wav_bytes=stream.getvalue(),
            sample_rate=sample_rate,
            duration_seconds=samples.size / sample_rate,
        )


def prepare_voice_review(
    output_dir: Path,
    *,
    kokoro: object,
    melo: object,
) -> dict[str, object]:
    """Create listener-safe, deterministic blind-review WAVs and score template."""
    for expected_engine, tts in (("kokoro", kokoro), ("melo", melo)):
        if getattr(tts, "engine", None) != expected_engine or not callable(
            getattr(tts, "synthesize_native", None)
        ):
            raise TypeError(f"{expected_engine} must expose synthesize_native()")
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    randomizer = random.Random(VOICE_REVIEW_SEED)
    engine_sources = [
        (tts, text, voice_id)
        for text in (
            "你好，我是声灵，很高兴陪你聊聊天。",
            "下午三点提醒我喝水，然后打开记事本。",
            "今天的 meeting 改到晚上八点，请不要忘记。",
        )
        for tts, voice_id in ((kokoro, 3), (melo, 0))
    ]
    randomizer.shuffle(engine_sources)
    engine_comparison: list[dict[str, object]] = []
    for index, (tts, text, voice_id) in enumerate(engine_sources, start=1):
        sample_id = f"engine-{index:03d}"
        audio = tts.synthesize_native(text, voice_id, 1.0)
        _write_review_wav(destination / f"{sample_id}.wav", audio)
        engine_comparison.append(
            {
                "sample_id": sample_id,
                "file": f"{sample_id}.wav",
                "naturalness": None,
                "intelligibility": None,
            }
        )
    voice_ids = list(KOKORO_REVIEW_VOICE_IDS)
    randomizer.shuffle(voice_ids)
    voice_style: list[dict[str, object]] = []
    for index, voice_id in enumerate(voice_ids, start=1):
        sample_id = f"voice-{index:03d}"
        _write_review_wav(
            destination / f"{sample_id}.wav",
            kokoro.synthesize_native(VOICE_REVIEW_SENTENCE, voice_id, 1.0),
        )
        voice_style.append(
            {
                "sample_id": sample_id,
                "file": f"{sample_id}.wav",
                "assigned_label": None,
                "naturalness": None,
                "intelligibility": None,
            }
        )
    template = {
        "schema_version": 1,
        "seed": VOICE_REVIEW_SEED,
        "engine_comparison": engine_comparison,
        "voice_style": voice_style,
        "required_voice_labels": ["清澈女声", "温柔女声", "沉稳男声", "阳光男声"],
        "scoring_rules": {
            "range": [1, 5],
            "minimum_selected_score": 3,
            "no_duplicate_assignments": True,
        },
    }
    (destination / "review-template.json").write_text(
        json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return template


def _write_review_wav(path: Path, audio: object) -> None:
    if not isinstance(audio, AudioChunk):
        raise TypeError("voice review synthesis must return AudioChunk")
    path.write_bytes(audio.wav_bytes)
