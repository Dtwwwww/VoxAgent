import io
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile

from voxagent.speech.tts import (
    AudioChunk,
    SherpaOfflineTts,
    _prepare_review_wav,
    prepare_voice_review,
)
from voxagent.speech.vad import ModelAssetError
from voxagent.speech.voice_catalog import VoiceCatalog


class FakeSynthesizer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, float]] = []

    def generate(self, text: str, sid: int, speed: float):
        self.calls.append((text, sid, speed))
        return type(
            "Generated",
            (),
            {"samples": np.array([0.0, 0.5, -0.5]), "sample_rate": 24000},
        )()


def _catalog(tmp_path) -> VoiceCatalog:
    path = tmp_path / "voices.json"
    path.write_text(
        """{"voices":[{"voice_key":"clear_female","display_name":"清澈女声","description":"明亮","gender":"female","engine":"kokoro","native_voice_id":3,"is_default":true,"previewable":true}]}""",
        encoding="utf-8",
    )
    return VoiceCatalog.load(path)


def test_tts_resolves_only_catalog_voice_keys_and_encodes_native_pcm16_wav(tmp_path):
    fake = FakeSynthesizer()
    tts = SherpaOfflineTts(fake, _catalog(tmp_path), engine="kokoro")

    audio = tts.synthesize("你好", "clear_female", 1.2)

    samples, sample_rate = soundfile.read(
        io.BytesIO(audio.wav_bytes), dtype="int16", always_2d=True
    )
    assert fake.calls == [("你好", 3, 1.2)]
    assert audio.sample_rate == 24000
    assert sample_rate == 24000
    assert samples.shape == (3, 1)
    assert samples.dtype == np.int16


@pytest.mark.parametrize("text", ["", "  "])
def test_tts_rejects_empty_text(tmp_path, text):
    with pytest.raises(ValueError, match="non-empty"):
        SherpaOfflineTts(FakeSynthesizer(), _catalog(tmp_path), engine="kokoro").synthesize(
            text, "clear_female", 1.0
        )


@pytest.mark.parametrize("speed", [0.79, 0.9, 1.01, 1.21])
def test_tts_rejects_speeds_outside_the_public_set(tmp_path, speed):
    with pytest.raises(ValueError, match="0.8, 1.0, 1.2"):
        SherpaOfflineTts(FakeSynthesizer(), _catalog(tmp_path), engine="kokoro").synthesize(
            "你好", "clear_female", speed
        )


def test_audio_chunk_rejects_non_positive_native_sample_rate():
    with pytest.raises(ValueError, match="positive"):
        AudioChunk(wav_bytes=b"wav", sample_rate=0, duration_seconds=0.1)


def _pcm16_wav(
    *,
    seconds: float,
    sample_rate: int = 24000,
    channels: int = 1,
    subtype: str = "PCM_16",
):
    stream = io.BytesIO()
    samples = np.zeros((round(seconds * sample_rate), channels), dtype=np.float32)
    soundfile.write(stream, samples, sample_rate, format="WAV", subtype=subtype)
    return stream.getvalue()


@pytest.mark.parametrize(
    ("seconds", "expected_seconds"),
    [(2.999, 3.0), (3.0, 3.0), (5.0, 5.0), (5.001, 5.0)],
)
def test_review_wav_has_a_safe_three_to_five_second_boundary(seconds, expected_seconds):
    wav = _prepare_review_wav(
        AudioChunk(_pcm16_wav(seconds=seconds), sample_rate=24000, duration_seconds=seconds)
    )
    info = soundfile.info(io.BytesIO(wav))

    assert info.format == "WAV"
    assert info.subtype == "PCM_16"
    assert info.channels == 1
    assert info.samplerate == 24000
    assert info.duration == pytest.approx(expected_seconds)


@pytest.mark.parametrize(
    "audio",
    [
        AudioChunk(_pcm16_wav(seconds=3.0, channels=2), sample_rate=24000, duration_seconds=3.0),
        AudioChunk(
            _pcm16_wav(seconds=3.0, subtype="PCM_24"), sample_rate=24000, duration_seconds=3.0
        ),
        AudioChunk(
            _pcm16_wav(seconds=3.0, sample_rate=16000),
            sample_rate=24000,
            duration_seconds=3.0,
        ),
    ],
)
def test_review_wav_rejects_invalid_format_or_native_rate(audio):
    with pytest.raises(ValueError, match="review WAV"):
        _prepare_review_wav(audio)


def test_review_wav_refuses_to_hard_cut_a_speaking_tail():
    stream = io.BytesIO()
    samples = np.zeros(round(5.001 * 24000), dtype=np.float32)
    samples[-1] = 0.5
    soundfile.write(stream, samples, 24000, format="WAV", subtype="PCM_16")

    with pytest.raises(ValueError, match="speaking audio"):
        _prepare_review_wav(AudioChunk(stream.getvalue(), 24000, 5.001))


def test_tts_factory_rejects_relative_model_directory_before_resolution():
    with pytest.raises(ValueError, match="absolute"):
        SherpaOfflineTts.from_model_dir(Path("relative-model"), 0, engine="melo")


def test_tts_factory_reports_missing_assets_with_absolute_paths(tmp_path):
    model_dir = (tmp_path / "melo").resolve()
    model_dir.mkdir()

    with pytest.raises(ModelAssetError) as error:
        SherpaOfflineTts.from_model_dir(model_dir, 0, engine="melo")

    assert str(model_dir / "model.onnx") in str(error.value)
    assert str(model_dir / "tokens.txt") in str(error.value)
    assert str(model_dir / "lexicon.txt") in str(error.value)


class _ReviewTts:
    def __init__(self, engine: str, wav_bytes: bytes) -> None:
        self.engine = engine
        self.wav_bytes = wav_bytes
        self.calls: list[tuple[str, int, float]] = []

    def synthesize_native(self, text: str, native_voice_id: int, speed: float) -> AudioChunk:
        self.calls.append((text, native_voice_id, speed))
        return AudioChunk(self.wav_bytes, sample_rate=24000, duration_seconds=0.1)


def test_voice_review_is_anonymous_deterministic_and_has_all_required_samples(tmp_path):
    stream = io.BytesIO()
    soundfile.write(stream, np.zeros(2400, dtype=np.float32), 24000, format="WAV", subtype="PCM_16")
    kokoro = _ReviewTts("kokoro", stream.getvalue())
    melo = _ReviewTts("melo", stream.getvalue())

    template = prepare_voice_review(tmp_path, kokoro=kokoro, melo=melo)

    payload = json.loads((tmp_path / "review-template.json").read_text(encoding="utf-8"))
    assert template == payload
    assert payload["seed"] == 20260830
    assert len(payload["engine_comparison"]) == 6
    assert len(payload["voice_style"]) == 8
    assert len(list(tmp_path.glob("*.wav"))) == 14
    assert len(kokoro.calls) == 11
    assert len(melo.calls) == 3
    assert {speed for _, _, speed in (*kokoro.calls, *melo.calls)} == {1.2}
    for wav in tmp_path.glob("*.wav"):
        info = soundfile.info(wav)
        assert info.format == "WAV"
        assert info.subtype == "PCM_16"
        assert info.channels == 1
        assert info.samplerate > 0
        assert 3.0 <= info.duration <= 5.0
    listener_view = json.dumps(payload, ensure_ascii=False)
    assert "kokoro" not in listener_view
    assert "melo" not in listener_view
    assert "native_voice_id" not in listener_view
