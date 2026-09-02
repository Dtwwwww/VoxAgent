from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from time import perf_counter

import numpy as np
import psutil
import soundfile

from voxagent.speech.asr import AsrEngine, PartialAsrEngine
from voxagent.speech.tts import TTS_BENCHMARK_TEXTS, AudioChunk

MIN_AUDIO_DURATION_SECONDS = 10.0
MAX_AUDIO_DURATION_SECONDS = 20.0
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")


@dataclass(frozen=True, slots=True)
class TimedResult[T]:
    label: str
    elapsed_seconds: float
    result: T

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def measure_call[T](label: str, operation: Callable[[], T]) -> TimedResult[T]:
    started = perf_counter()
    result = operation()
    finished = perf_counter()
    return TimedResult(label, finished - started, result)


class FixtureChecksumError(ValueError):
    """The required user-recorded ASR fixture is missing or was modified."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _nearest_rank_p95(values: list[float]) -> float:
    return sorted(values)[math.ceil(0.95 * len(values)) - 1]


def validate_fixture_checksum(wav: Path, checksums_path: Path) -> str:
    wav = Path(wav).resolve()
    checksums_path = Path(checksums_path).resolve()
    if wav.name != "mandarin-command.wav":
        raise FixtureChecksumError("ASR benchmark requires mandarin-command.wav")
    if not wav.is_file():
        raise FixtureChecksumError(f"Missing required user fixture: {wav}")
    if not checksums_path.is_file():
        raise FixtureChecksumError(f"Missing fixture checksum manifest: {checksums_path}")
    try:
        expected = json.loads(checksums_path.read_text(encoding="utf-8"))[wav.name]
    except (KeyError, json.JSONDecodeError) as error:
        raise FixtureChecksumError(
            f"checksums.json must contain a SHA-256 for {wav.name}"
        ) from error
    if not isinstance(expected, str) or _SHA256.fullmatch(expected) is None:
        raise FixtureChecksumError(f"checksums.json has an invalid SHA-256 for {wav.name}")
    actual = _sha256(wav)
    if actual.lower() != expected.lower():
        raise FixtureChecksumError(f"Fixture SHA-256 mismatch for {wav.name}")
    return actual


def select_partial_asr_model(sensevoice_p95_seconds: float) -> str:
    if sensevoice_p95_seconds <= 0.3:
        return "sensevoice-int8"
    return "streaming-paraformer-bilingual-zh-en"


def _default_read_wav(path: Path) -> tuple[np.ndarray, int]:
    samples, sample_rate = soundfile.read(path, dtype="float32", always_2d=True)
    if samples.shape[1] != 1:
        raise ValueError("ASR benchmark WAV must be mono")
    return samples[:, 0], int(sample_rate)


def _load_benchmark_audio(
    wav: Path,
    checksums_path: Path,
    read_wav: Callable[[Path], tuple[np.ndarray, int]],
) -> tuple[np.ndarray, int, str]:
    fixture_sha256 = validate_fixture_checksum(wav, checksums_path)
    samples, sample_rate = read_wav(Path(wav))
    if sample_rate != 16000 or samples.ndim != 1 or samples.dtype != np.float32:
        raise ValueError("ASR benchmark WAV must be 16 kHz mono float32 audio")
    if samples.size == 0:
        raise ValueError("ASR benchmark WAV must contain audio")
    duration_seconds = samples.size / sample_rate
    if not MIN_AUDIO_DURATION_SECONDS <= duration_seconds <= MAX_AUDIO_DURATION_SECONDS:
        raise ValueError("ASR benchmark WAV must be 10 to 20 seconds")
    if samples.size % 320:
        raise ValueError("ASR benchmark WAV must contain complete 20 ms frames")
    return samples, sample_rate, fixture_sha256


def run_partial_probe(
    wav: Path,
    checksums_path: Path,
    *,
    partial_asr: PartialAsrEngine,
    read_wav: Callable[[Path], tuple[np.ndarray, int]] = _default_read_wav,
    peak_rss_bytes: Callable[[], int] = lambda: psutil.Process().memory_info().rss,
) -> tuple[str, float]:
    samples, _, _ = _load_benchmark_audio(wav, checksums_path, read_wav)
    latencies: list[float] = []
    for pass_index in range(6):
        partial_asr.reset()
        peak_rss_bytes()
        for frame in np.split(samples, samples.size // 320):
            timed = measure_call("partial_asr", lambda frame=frame: partial_asr.accept(frame))
            peak_rss_bytes()
            if pass_index and timed.result.updated:
                latencies.append(timed.elapsed_seconds)
    return (
        partial_asr.model_id,
        _nearest_rank_p95(latencies) if latencies else float("inf"),
    )


def run_asr_benchmark(
    wav: Path,
    checksums_path: Path,
    *,
    final_asr: AsrEngine,
    partial_asr: PartialAsrEngine | None = None,
    read_wav: Callable[[Path], tuple[np.ndarray, int]] = _default_read_wav,
    peak_rss_bytes: Callable[[], int] = lambda: psutil.Process().memory_info().rss,
) -> dict[str, object]:
    samples, sample_rate, fixture_sha256 = _load_benchmark_audio(wav, checksums_path, read_wav)

    audio_duration = samples.size / sample_rate
    final_latencies: list[float] = []
    partial_latencies: list[float] = []
    rtfs: list[float] = []
    transcript = ""
    peak_rss = peak_rss_bytes()
    for pass_index in range(6):
        if partial_asr is not None:
            partial_asr.reset()
            peak_rss = max(peak_rss, peak_rss_bytes())
            for frame in np.split(samples, samples.size // 320):
                timed_partial = measure_call(
                    "partial_asr", lambda frame=frame: partial_asr.accept(frame)
                )
                peak_rss = max(peak_rss, peak_rss_bytes())
                if pass_index and timed_partial.result.updated:
                    partial_latencies.append(timed_partial.elapsed_seconds)
        timed_final = measure_call("final_asr", lambda: final_asr.transcribe(samples, sample_rate))
        peak_rss = max(peak_rss, peak_rss_bytes())
        if pass_index:
            final_latencies.append(timed_final.elapsed_seconds)
            rtfs.append(timed_final.elapsed_seconds / audio_duration)
            transcript = timed_final.result.text

    if not transcript.strip():
        raise ValueError("ASR benchmark transcript must be non-empty")
    return {
        "schema_version": 1,
        "fixture": "benchmarks/fixtures/mandarin-command.wav",
        "fixture_sha256": fixture_sha256,
        "model_id": final_asr.model_id,
        "partial_model_id": partial_asr.model_id if partial_asr is not None else None,
        "run_count": 5,
        "audio_duration_seconds": round(audio_duration, 3),
        "transcript": transcript,
        "latency_seconds": {
            "final_asr_median": round(median(final_latencies), 3),
            "final_asr_p95": round(_nearest_rank_p95(final_latencies), 3),
            "partial_update_median": round(median(partial_latencies), 3)
            if partial_latencies
            else None,
            "partial_update_p95": round(_nearest_rank_p95(partial_latencies), 3)
            if partial_latencies
            else None,
        },
        "real_time_factor": {
            "median": round(median(rtfs), 3),
            "p95": round(_nearest_rank_p95(rtfs), 3),
        },
        "peak_process_rss_bytes": peak_rss,
    }


def run_tts_benchmark(
    tts: object,
    *,
    native_voice_id: int,
    texts: tuple[str, ...] = TTS_BENCHMARK_TEXTS,
    peak_rss_bytes: Callable[[], int] = lambda: psutil.Process().memory_info().rss,
) -> dict[str, object]:
    """Measure one warm-up and five native-rate TTS runs for each fixed text."""
    engine = getattr(tts, "engine", None)
    synthesize_native = getattr(tts, "synthesize_native", None)
    if engine not in {"kokoro", "melo"} or not callable(synthesize_native):
        raise TypeError("tts must expose an engine and synthesize_native()")
    if not texts:
        raise ValueError("TTS benchmark requires fixed texts")
    peak_rss = peak_rss_bytes()
    reports: list[dict[str, object]] = []
    for text in texts:
        latencies: list[float] = []
        durations: list[float] = []
        rtfs: list[float] = []
        for pass_index in range(6):
            timed = measure_call(
                "tts_first_audio",
                lambda text=text: synthesize_native(text, native_voice_id, 1.0),
            )
            peak_rss = max(peak_rss, peak_rss_bytes())
            audio = timed.result
            if not isinstance(audio, AudioChunk):
                raise TypeError("TTS benchmark must receive AudioChunk results")
            if pass_index:
                latencies.append(timed.elapsed_seconds)
                durations.append(audio.duration_seconds)
                rtfs.append(timed.elapsed_seconds / audio.duration_seconds)
        reports.append(
            {
                "text": text,
                "first_audio_latency_seconds": {
                    "p50": round(median(latencies), 3),
                    "p95": round(_nearest_rank_p95(latencies), 3),
                },
                "generated_duration_seconds": {
                    "p50": round(median(durations), 3),
                    "p95": round(_nearest_rank_p95(durations), 3),
                },
                "synthesis_real_time_factor": {
                    "p50": round(median(rtfs), 3),
                    "p95": round(_nearest_rank_p95(rtfs), 3),
                },
            }
        )
    return {
        "schema_version": 1,
        "engine": engine,
        "native_voice_id": native_voice_id,
        "run_count": 5,
        "texts": reports,
        "peak_process_rss_bytes": peak_rss,
    }


def _baseline_candidate(
    baseline_path: Path,
    artifact_path: Path,
    artifact: dict[str, object],
    artifact_sha256: str,
) -> dict[str, object]:
    try:
        artifact_reference = artifact_path.relative_to(baseline_path.parent.parent).as_posix()
    except ValueError as error:
        raise ValueError(
            "ASR artifact must be stored under the repository benchmarks directory"
        ) from error
    return {
        "model_id": artifact["model_id"],
        "artifact_path": artifact_reference,
        "artifact_sha256": artifact_sha256,
        "run_count": artifact["run_count"],
        "transcript": artifact["transcript"],
        "timing": artifact.get("latency_seconds"),
        "real_time_factor": artifact.get("real_time_factor"),
        "audio_duration_seconds": artifact.get("audio_duration_seconds"),
        "partial_model_id": artifact.get("partial_model_id"),
    }


def prepare_asr_baseline_update(
    baseline_path: Path,
    artifact_path: Path,
    artifact: dict[str, object],
    artifact_bytes: bytes,
) -> str:
    baseline_path = Path(baseline_path).resolve()
    artifact_path = Path(artifact_path).resolve()
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    candidate = _baseline_candidate(
        baseline_path, artifact_path, artifact, hashlib.sha256(artifact_bytes).hexdigest()
    )
    existing = [
        item
        for item in payload.get("asr_candidates", [])
        if item.get("model_id") != candidate["model_id"]
    ]
    existing.append(candidate)
    payload["asr_candidates"] = existing
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


@dataclass(slots=True)
class _PublicationTarget:
    target: Path
    contents: bytes
    existed: bool
    temporary: Path | None = None
    backup: Path | None = None
    backup_contains_original: bool = False
    published: bool = False


def _write_publication_temporary(target: Path, contents: bytes) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".voxagent-tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _reserve_publication_backup(target: Path) -> Path:
    descriptor, backup_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".voxagent-backup",
        dir=target.parent,
    )
    os.close(descriptor)
    return Path(backup_name)


def _remove_owned_publication_file(path: Path | None) -> None:
    if path is not None:
        path.unlink(missing_ok=True)


def publish_asr_benchmark(
    artifact_path: Path,
    artifact_bytes: bytes,
    baseline_path: Path,
    baseline_bytes: bytes,
    *,
    replace: Callable[[Path, Path], None] | None = None,
) -> None:
    """Publish ASR artifact and baseline together, restoring both on failure."""
    artifact_path = Path(artifact_path).resolve()
    baseline_path = Path(baseline_path).resolve()
    if artifact_path == baseline_path:
        raise ValueError("ASR artifact and baseline paths must be distinct")
    for target in (artifact_path, baseline_path):
        if not target.parent.is_dir():
            raise ValueError(f"ASR publication directory does not exist: {target.parent}")
        if target.exists() and not target.is_file():
            raise ValueError(f"ASR publication target must be a file: {target}")

    replace_file = replace or (lambda source, destination: os.replace(source, destination))
    targets = [
        _PublicationTarget(artifact_path, artifact_bytes, artifact_path.exists()),
        _PublicationTarget(baseline_path, baseline_bytes, baseline_path.exists()),
    ]
    rollback_errors: list[OSError] = []
    try:
        for publication in targets:
            publication.temporary = _write_publication_temporary(
                publication.target, publication.contents
            )
        for publication in targets:
            if publication.existed:
                publication.backup = _reserve_publication_backup(publication.target)
        for publication in targets:
            if publication.backup is not None:
                replace_file(publication.target, publication.backup)
                publication.backup_contains_original = True
        for publication in targets:
            assert publication.temporary is not None
            replace_file(publication.temporary, publication.target)
            publication.temporary = None
            publication.published = True
    except BaseException as error:
        for publication in reversed(targets):
            try:
                if publication.backup_contains_original:
                    assert publication.backup is not None
                    replace_file(publication.backup, publication.target)
                    publication.backup_contains_original = False
                elif not publication.existed and publication.published:
                    _remove_owned_publication_file(publication.target)
            except OSError as rollback_error:
                rollback_errors.append(rollback_error)
        if rollback_errors:
            raise RuntimeError("ASR benchmark publication rollback failed") from error
        raise
    else:
        for publication in targets:
            _remove_owned_publication_file(publication.backup)
            publication.backup = None
    finally:
        for publication in targets:
            _remove_owned_publication_file(publication.temporary)
            if not publication.backup_contains_original:
                _remove_owned_publication_file(publication.backup)


def update_asr_baseline(baseline_path: Path, artifact_path: Path) -> None:
    artifact_path = Path(artifact_path).resolve()
    artifact_bytes = artifact_path.read_bytes()
    artifact = json.loads(artifact_bytes.decode("utf-8"))
    baseline_path = Path(baseline_path).resolve()
    baseline_path.write_text(
        prepare_asr_baseline_update(baseline_path, artifact_path, artifact, artifact_bytes),
        encoding="utf-8",
    )


def publish_tts_benchmark(
    artifact_path: Path,
    artifact_bytes: bytes,
    baseline_path: Path,
    baseline_bytes: bytes,
    *,
    replace: Callable[[Path, Path], None] | None = None,
) -> None:
    """Publish a TTS artifact and selected baseline together, or restore both."""
    publish_asr_benchmark(
        artifact_path,
        artifact_bytes,
        baseline_path,
        baseline_bytes,
        replace=replace,
    )
