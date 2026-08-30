from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from voxagent.diagnostics.speech_benchmark import measure_call
from voxagent.speech.model_manifest import SPEECH_MODELS, SpeechModel


def test_speech_manifest_has_unique_names_and_https_urls():
    assert len({model.name for model in SPEECH_MODELS}) == 3
    assert all(model.url.startswith("https://github.com/k2-fsa/") for model in SPEECH_MODELS)


def test_speech_manifest_has_exact_immutable_slotted_models():
    assert SPEECH_MODELS == (
        SpeechModel(
            name="sensevoice-int8",
            archive_name="sensevoice-int8.tar.bz2",
            url=(
                "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
                "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2"
            ),
            directory_name="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
        ),
        SpeechModel(
            name="kokoro-int8-zh-en",
            archive_name="kokoro-int8-multi-lang-v1_1.tar.bz2",
            url=(
                "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
                "kokoro-int8-multi-lang-v1_1.tar.bz2"
            ),
            directory_name="kokoro-int8-multi-lang-v1_1",
        ),
        SpeechModel(
            name="melo-zh-en",
            archive_name="vits-melo-tts-zh_en.tar.bz2",
            url=(
                "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
                "vits-melo-tts-zh_en.tar.bz2"
            ),
            directory_name="vits-melo-tts-zh_en",
        ),
    )
    assert not hasattr(SPEECH_MODELS[0], "__dict__")
    with pytest.raises(FrozenInstanceError):
        SPEECH_MODELS[0].name = "changed"


def test_downloader_checks_tar_exit_code_before_validating_and_removing_archive():
    script = (Path(__file__).parents[3] / "scripts" / "download_speech_models.ps1").read_text(
        encoding="utf-8"
    )
    tar_index = script.index("tar.exe -xjf $archive -C $modelRoot")
    exit_guard_index = script.index("if ($LASTEXITCODE -ne 0)")
    target_check_index = script.index("if (-not (Test-Path -LiteralPath $target))")
    remove_index = script.index("Remove-Item -LiteralPath $archive")

    assert tar_index < exit_guard_index < target_check_index < remove_index
    exit_guard = script[exit_guard_index:target_check_index]
    assert 'throw "Extraction failed for $($model.Name)"' in exit_guard

def test_measure_call_records_elapsed_and_result(monkeypatch):
    times = iter([5.0, 5.25])
    monkeypatch.setattr("voxagent.diagnostics.speech_benchmark.perf_counter", lambda: next(times))
    timed = measure_call("tts", lambda: 24000)
    assert timed.label == "tts"
    assert timed.elapsed_seconds == 0.25
    assert timed.result == 24000
