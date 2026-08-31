from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from time import perf_counter

import numpy as np
import psutil
import soundfile

from voxagent.speech.asr import AsrEngine, PartialAsrEngine

TTS_BENCHMARK_TEXTS = (
    "你好，我是声灵，很高兴陪你聊聊天。",
    "下午三点提醒我喝水，然后打开记事本。",
    "今天的 meeting 改到晚上八点，请不要忘记。",
)
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


def update_asr_baseline(baseline_path: Path, artifact_path: Path) -> None:
    artifact_path = Path(artifact_path).resolve()
    artifact_bytes = artifact_path.read_bytes()
    artifact = json.loads(artifact_bytes.decode("utf-8"))
    baseline_path = Path(baseline_path).resolve()
    baseline_path.write_text(
        prepare_asr_baseline_update(baseline_path, artifact_path, artifact, artifact_bytes),
        encoding="utf-8",
    )
