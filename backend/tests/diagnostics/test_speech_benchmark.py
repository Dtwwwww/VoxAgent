import hashlib
import os
from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from voxagent.diagnostics.speech_benchmark import (
    FixtureChecksumError,
    measure_call,
    publish_asr_benchmark,
    publish_tts_benchmark,
    run_asr_benchmark,
    run_tts_benchmark,
    select_partial_asr_model,
    update_asr_baseline,
    validate_fixture_checksum,
)
from voxagent.speech.model_manifest import SPEECH_MODELS, SpeechModel
from voxagent.speech.tts import AudioChunk


class _FakeTts:
    engine = "kokoro"

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, float]] = []

    def synthesize_native(self, text: str, native_voice_id: int, speed: float) -> AudioChunk:
        self.calls.append((text, native_voice_id, speed))
        return AudioChunk(wav_bytes=b"valid wav bytes", sample_rate=24000, duration_seconds=1.5)


def test_tts_benchmark_runs_one_warmup_and_five_measured_passes_and_keeps_peak_rss():
    tts = _FakeTts()
    rss = iter([10, 100, *([20] * 18)])

    report = run_tts_benchmark(tts, native_voice_id=3, peak_rss_bytes=lambda: next(rss))

    assert len(tts.calls) == 18
    assert {speed for _, _, speed in tts.calls} == {1.0}
    assert report["engine"] == "kokoro"
    assert report["native_voice_id"] == 3
    assert report["run_count"] == 5
    assert report["peak_process_rss_bytes"] == 100
    assert len(report["texts"]) == 3
    assert all(item["first_audio_latency_seconds"]["p95"] >= 0 for item in report["texts"])


def test_tts_publication_restores_the_artifact_when_baseline_publication_fails(tmp_path):
    artifact = tmp_path / "kokoro.json"
    baseline = tmp_path / "baseline.json"
    artifact.write_bytes(b"old artifact")
    baseline.write_bytes(b"old baseline")

    def fail_second_write(source, destination):
        if destination == baseline and source.name.endswith(".voxagent-tmp"):
            raise OSError("second write failed")
        os.replace(source, destination)

    with pytest.raises(OSError, match="second write failed"):
        publish_tts_benchmark(
            artifact,
            b"new artifact",
            baseline,
            b"new baseline",
            replace=fail_second_write,
        )

    assert artifact.read_bytes() == b"old artifact"
    assert baseline.read_bytes() == b"old baseline"


def test_speech_manifest_has_unique_names_and_https_urls():
    assert len({model.name for model in SPEECH_MODELS}) == 5
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
            name="streaming-paraformer-bilingual-zh-en",
            format="archive",
            archive_name="sherpa-onnx-streaming-paraformer-bilingual-zh-en.tar.bz2",
            url=(
                "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
                "sherpa-onnx-streaming-paraformer-bilingual-zh-en.tar.bz2"
            ),
            directory_name="sherpa-onnx-streaming-paraformer-bilingual-zh-en",
            version="2024-03-10",
            source="k2-fsa/sherpa-onnx release asr-models",
            archive_sha256="5462a1fce42693deae572af1e8c4687124b12aa85fe61ff4d3168bb5280e205f",
            required_files=("encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt"),
            archive_size_bytes=1047319737,
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


def test_measure_call_preserves_precision_used_for_partial_selection(monkeypatch):
    times = iter([5.0, 5.3004])
    monkeypatch.setattr("voxagent.diagnostics.speech_benchmark.perf_counter", lambda: next(times))

    timed = measure_call("partial", lambda: "text")

    assert timed.elapsed_seconds == pytest.approx(0.3004)


def test_benchmark_validates_checksum_before_reading_audio(tmp_path, monkeypatch):
    wav = tmp_path / "mandarin-command.wav"
    wav.write_bytes(b"test fixture bytes")
    checksums = tmp_path / "checksums.json"
    checksums.write_text('{"mandarin-command.wav": "' + "0" * 64 + '"}', encoding="utf-8")
    monkeypatch.setattr(
        "voxagent.diagnostics.speech_benchmark.soundfile.read",
        lambda *args, **kwargs: pytest.fail("audio must not load before checksum validation"),
    )

    with pytest.raises(FixtureChecksumError, match="SHA-256"):
        run_asr_benchmark(wav, checksums, final_asr=object())


def test_benchmark_runs_one_warmup_and_five_measured_passes(tmp_path):
    from voxagent.speech.asr import AsrResult

    wav = tmp_path / "mandarin-command.wav"
    wav.write_bytes(b"verified test fixture")
    checksums = tmp_path / "checksums.json"
    checksums.write_text(
        '{"mandarin-command.wav": "' + hashlib.sha256(wav.read_bytes()).hexdigest() + '"}',
        encoding="utf-8",
    )

    class FakeFinalAsr:
        model_id = "sensevoice-int8"

        def __init__(self) -> None:
            self.calls = 0

        def transcribe(self, samples, sample_rate):
            self.calls += 1
            return AsrResult(text="打开音乐", language="zh")

    class FakePartialAsr:
        model_id = "streaming-paraformer-bilingual-zh-en"

        def __init__(self) -> None:
            self.calls = 0

        def reset(self):
            return None

        def accept(self, samples):
            self.calls += 1
            from voxagent.speech.asr import PartialAsrResult

            return PartialAsrResult(text="打开", updated=True)

    final = FakeFinalAsr()
    partial = FakePartialAsr()
    report = run_asr_benchmark(
        wav,
        checksums,
        final_asr=final,
        partial_asr=partial,
        read_wav=lambda _: (np.zeros(160000, dtype=np.float32), 16000),
        peak_rss_bytes=lambda: 1234,
    )

    assert final.calls == 6
    assert partial.calls == 3000
    assert report["run_count"] == 5
    assert report["transcript"] == "打开音乐"
    assert report["audio_duration_seconds"] == 10.0
    assert report["peak_process_rss_bytes"] == 1234
    assert report["model_id"] == "sensevoice-int8"
    assert report["partial_model_id"] == "streaming-paraformer-bilingual-zh-en"
    assert report["latency_seconds"]["partial_update_p95"] is not None


def test_partial_selection_uses_sensevoice_only_when_p95_is_at_most_300ms():
    assert select_partial_asr_model(0.3) == "sensevoice-int8"
    assert select_partial_asr_model(0.3004) == "streaming-paraformer-bilingual-zh-en"
    assert select_partial_asr_model(0.301) == "streaming-paraformer-bilingual-zh-en"


def test_fixture_checksum_requires_named_user_fixture(tmp_path):
    checksums = tmp_path / "checksums.json"
    checksums.write_text("{}", encoding="utf-8")
    wav = tmp_path / "wrong.wav"
    wav.write_bytes(b"fixture")

    with pytest.raises(FixtureChecksumError, match="mandarin-command.wav"):
        validate_fixture_checksum(wav, checksums)


def test_fixture_checksum_rejects_non_hex_digest(tmp_path):
    checksums = tmp_path / "checksums.json"
    checksums.write_text('{"mandarin-command.wav": "' + "g" * 64 + '"}', encoding="utf-8")
    wav = tmp_path / "mandarin-command.wav"
    wav.write_bytes(b"fixture")

    with pytest.raises(FixtureChecksumError, match="invalid SHA-256"):
        validate_fixture_checksum(wav, checksums)


@pytest.mark.parametrize("seconds", [9.999, 20.001])
def test_benchmark_rejects_fixture_outside_ten_to_twenty_seconds(tmp_path, seconds):
    wav = tmp_path / "mandarin-command.wav"
    wav.write_bytes(b"verified test fixture")
    checksums = tmp_path / "checksums.json"
    checksums.write_text(
        '{"mandarin-command.wav": "' + hashlib.sha256(wav.read_bytes()).hexdigest() + '"}',
        encoding="utf-8",
    )
    samples = np.zeros(round(seconds * 16000), dtype=np.float32)

    with pytest.raises(ValueError, match="10 to 20 seconds"):
        run_asr_benchmark(
            wav,
            checksums,
            final_asr=object(),
            read_wav=lambda _: (samples, 16000),
        )


def test_benchmark_retains_the_highest_rss_sample(tmp_path):
    from voxagent.speech.asr import AsrResult

    wav = tmp_path / "mandarin-command.wav"
    wav.write_bytes(b"verified test fixture")
    checksums = tmp_path / "checksums.json"
    checksums.write_text(
        '{"mandarin-command.wav": "' + hashlib.sha256(wav.read_bytes()).hexdigest() + '"}',
        encoding="utf-8",
    )
    rss_values = iter([10, 100, *([20] * 10_000)])

    class FakeFinalAsr:
        model_id = "sensevoice-int8"

        def transcribe(self, samples, sample_rate):
            return AsrResult(text="打开音乐")

    report = run_asr_benchmark(
        wav,
        checksums,
        final_asr=FakeFinalAsr(),
        read_wav=lambda _: (np.zeros(160000, dtype=np.float32), 16000),
        peak_rss_bytes=lambda: next(rss_values),
    )

    assert report["peak_process_rss_bytes"] == 100


def test_baseline_update_copies_the_measured_asr_artifact_metadata(tmp_path):
    baseline = tmp_path / "benchmarks" / "target-machine-baseline.json"
    baseline.parent.mkdir()
    baseline.write_text('{"asr_candidates": []}', encoding="utf-8")
    artifact = baseline.parent / "sensevoice-int8.json"
    artifact.write_text(
        '{"model_id": "sensevoice-int8", "run_count": 5, "transcript": "打开音乐"}',
        encoding="utf-8",
    )

    update_asr_baseline(baseline, artifact)

    candidate = __import__("json").loads(baseline.read_text(encoding="utf-8"))["asr_candidates"][0]
    assert candidate["model_id"] == "sensevoice-int8"
    assert candidate["artifact_path"] == "benchmarks/sensevoice-int8.json"
    assert candidate["run_count"] == 5
    assert candidate["transcript"] == "打开音乐"
    assert len(candidate["artifact_sha256"]) == 64


@pytest.mark.parametrize(
    ("artifact_original", "baseline_original"),
    [
        (b"old artifact", b"old baseline"),
        (None, b"old baseline"),
        (b"old artifact", None),
        (None, None),
    ],
)
def test_atomic_publish_restores_both_targets_when_second_publication_replace_fails(
    tmp_path,
    artifact_original,
    baseline_original,
):
    artifact = tmp_path / "artifacts" / "sensevoice-int8.json"
    baseline = tmp_path / "baselines" / "target-machine-baseline.json"
    artifact.parent.mkdir()
    baseline.parent.mkdir()
    if artifact_original is not None:
        artifact.write_bytes(artifact_original)
    if baseline_original is not None:
        baseline.write_bytes(baseline_original)

    def fail_baseline_publication(source, destination):
        if source.name.endswith(".voxagent-tmp") and destination == baseline:
            raise OSError("simulated second publication failure")
        os.replace(source, destination)

    with pytest.raises(OSError, match="simulated second publication failure"):
        publish_asr_benchmark(
            artifact,
            b"new artifact",
            baseline,
            b"new baseline",
            replace=fail_baseline_publication,
        )

    if artifact_original is None:
        assert not artifact.exists()
    else:
        assert artifact.read_bytes() == artifact_original
    if baseline_original is None:
        assert not baseline.exists()
    else:
        assert baseline.read_bytes() == baseline_original
    assert not list(tmp_path.rglob("*.voxagent-tmp"))
    assert not list(tmp_path.rglob("*.voxagent-backup"))


def test_atomic_publish_writes_both_targets_and_cleans_owned_files(tmp_path):
    artifact = tmp_path / "artifacts" / "sensevoice-int8.json"
    baseline = tmp_path / "baselines" / "target-machine-baseline.json"
    artifact.parent.mkdir()
    baseline.parent.mkdir()
    artifact.write_bytes(b"old artifact")
    baseline.write_bytes(b"old baseline")

    publish_asr_benchmark(artifact, b"new artifact", baseline, b"new baseline")

    assert artifact.read_bytes() == b"new artifact"
    assert baseline.read_bytes() == b"new baseline"
    assert not list(tmp_path.rglob("*.voxagent-tmp"))
    assert not list(tmp_path.rglob("*.voxagent-backup"))
