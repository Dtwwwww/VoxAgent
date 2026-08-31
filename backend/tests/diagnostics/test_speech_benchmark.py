from dataclasses import FrozenInstanceError

import pytest

from voxagent.diagnostics.speech_benchmark import measure_call
from voxagent.speech.model_manifest import SPEECH_MODELS, SpeechModel


def test_speech_manifest_has_unique_names_and_https_urls():
    assert len({model.name for model in SPEECH_MODELS}) == 4
    assert all(model.url.startswith("https://github.com/k2-fsa/") for model in SPEECH_MODELS)


def test_speech_manifest_has_exact_immutable_slotted_models():
    assert SPEECH_MODELS == (
        SpeechModel(
            name="sensevoice-int8",
            format="archive",
            archive_name="sensevoice-int8.tar.bz2",
            url=(
                "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
                "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2"
            ),
            directory_name="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
            version="2024-07-17",
            source="k2-fsa/sherpa-onnx release asr-models",
            archive_sha256="7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e",
            required_files=("model.int8.onnx", "tokens.txt"),
        ),
        SpeechModel(
            name="kokoro-int8-zh-en",
            format="archive",
            archive_name="kokoro-int8-multi-lang-v1_1.tar.bz2",
            url=(
                "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
                "kokoro-int8-multi-lang-v1_1.tar.bz2"
            ),
            directory_name="kokoro-int8-multi-lang-v1_1",
            version="1.1",
            source="k2-fsa/sherpa-onnx release tts-models",
            archive_sha256="a1e94694776049035c4f2c6529f003aaece993c76aae9a78995831c3c4dcafc6",
            required_files=(
                "model.int8.onnx",
                "voices.bin",
                "tokens.txt",
                "lexicon-zh.txt",
            ),
        ),
        SpeechModel(
            name="melo-zh-en",
            format="archive",
            archive_name="vits-melo-tts-zh_en.tar.bz2",
            url=(
                "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
                "vits-melo-tts-zh_en.tar.bz2"
            ),
            directory_name="vits-melo-tts-zh_en",
            version="vits-melo-tts-zh_en",
            source="k2-fsa/sherpa-onnx release tts-models",
            archive_sha256="e58351ed7149f290a54534538badd4077cdbe6fddc964b24d0bee870415d1514",
            required_files=("model.onnx", "tokens.txt", "lexicon.txt"),
        ),
        SpeechModel(
            name="silero-vad",
            format="file",
            archive_name="silero_vad.onnx",
            url=(
                "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
                "silero_vad.onnx"
            ),
            directory_name="silero-vad",
            version="silero_vad.onnx",
            source="k2-fsa/sherpa-onnx release asr-models (MIT)",
            archive_sha256="9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
            required_files=("silero_vad.onnx",),
        ),
    )
    assert not hasattr(SPEECH_MODELS[0], "__dict__")
    with pytest.raises(FrozenInstanceError):
        SPEECH_MODELS[0].name = "changed"


def test_measure_call_records_elapsed_and_result(monkeypatch):
    times = iter([5.0, 5.25])
    monkeypatch.setattr("voxagent.diagnostics.speech_benchmark.perf_counter", lambda: next(times))
    timed = measure_call("tts", lambda: 24000)
    assert timed.label == "tts"
    assert timed.elapsed_seconds == 0.25
    assert timed.result == 24000
