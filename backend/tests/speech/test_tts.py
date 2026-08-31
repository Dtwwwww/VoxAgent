import io
import json

import numpy as np
import pytest
import soundfile

from voxagent.speech.tts import AudioChunk, SherpaOfflineTts, prepare_voice_review
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
    listener_view = json.dumps(payload, ensure_ascii=False)
    assert "kokoro" not in listener_view
    assert "melo" not in listener_view
    assert "native_voice_id" not in listener_view
