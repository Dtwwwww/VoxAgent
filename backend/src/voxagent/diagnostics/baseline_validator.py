from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

from voxagent.diagnostics.selection import (
    DEFAULT_MODEL,
    QUALITY_MODEL,
    CandidateMetric,
    select_llm,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _nearest_rank_p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def _artifact_path(baseline_path: Path, recorded_path: str) -> Path:
    return baseline_path.parent.parent / Path(recorded_path)


def _timestamp_100ns(value: str) -> int:
    match = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z", value)
    if match is None:
        raise ValueError(f"Expected a UTC Z timestamp, got {value!r}")
    whole_seconds = int(datetime.fromisoformat(match.group(1)).replace(tzinfo=UTC).timestamp())
    fractional = (match.group(2) or "").ljust(7, "0")[:7]
    return whole_seconds * 10_000_000 + int(fractional)


def _compare(
    issues: list[str],
    label: str,
    actual: object,
    expected: object,
) -> None:
    if actual != expected:
        issues.append(f"{label}: expected {expected!r}, recomputed {actual!r}")


def _validate_candidate(
    baseline_path: Path,
    candidate: dict[str, object],
    issues: list[str],
) -> None:
    model_id = str(candidate["model_id"])
    timing_provenance = candidate["timing_provenance"]
    timing_path = _artifact_path(baseline_path, timing_provenance["artifact_path"])
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    _compare(issues, f"{model_id}.timing.model", timing["model"], model_id)
    _compare(
        issues,
        f"{model_id}.timing.artifact_sha256",
        _sha256(timing_path),
        timing_provenance["artifact_sha256"],
    )
    runs = timing["runs"]
    ttft = [float(run["ttft_seconds"]) for run in runs]
    total = [float(run["total_seconds"]) for run in runs]
    expected_timing = candidate["timing_seconds"]
    _compare(issues, f"{model_id}.run_count", len(runs), candidate["run_count"])
    _compare(issues, f"{model_id}.p50_ttft", median(ttft), expected_timing["p50_ttft"])
    _compare(
        issues,
        f"{model_id}.p95_ttft",
        _nearest_rank_p95(ttft),
        expected_timing["p95_ttft"],
    )
    _compare(issues, f"{model_id}.p50_total", median(total), expected_timing["p50_total"])
    _compare(
        issues,
        f"{model_id}.p95_total",
        _nearest_rank_p95(total),
        expected_timing["p95_total"],
    )

    resource_provenance = candidate["resource_provenance"]
    probe_path = _artifact_path(baseline_path, resource_provenance["probe_path"])
    probe = json.loads(probe_path.read_text(encoding="utf-8"))
    _compare(issues, f"{model_id}.probe.model_id", probe["model_id"], model_id)
    _compare(
        issues,
        f"{model_id}.probe.probe_sha256",
        _sha256(probe_path),
        resource_provenance["probe_sha256"],
    )
    samples = probe["samples"]
    peak_rss_bytes = max(sum(sample["runner_rss_bytes"]) for sample in samples)
    peak_vram_mib = max(sample["vram_mib"] for sample in samples)
    times = [_timestamp_100ns(sample["timestamp_utc"]) for sample in samples]
    intervals = [
        (current - previous) / 10_000
        for previous, current in zip(times, times[1:], strict=False)
    ]
    interval_summary = {
        "minimum": round(min(intervals), 3),
        "median": round(median(intervals), 3),
        "maximum": round(max(intervals), 3),
    }
    _compare(
        issues,
        f"{model_id}.probe.sample_count",
        len(samples),
        resource_provenance["sample_count"],
    )
    _compare(
        issues,
        f"{model_id}.probe.configured_interval",
        probe["configured_sample_interval_ms"],
        resource_provenance["sampler_configured_interval_ms"],
    )
    _compare(
        issues,
        f"{model_id}.probe.started_utc",
        samples[0]["timestamp_utc"],
        resource_provenance["probe_started_utc"],
    )
    _compare(
        issues,
        f"{model_id}.probe.ended_utc",
        samples[-1]["timestamp_utc"],
        resource_provenance["probe_ended_utc"],
    )
    _compare(
        issues,
        f"{model_id}.probe.observed_sample_interval_ms",
        interval_summary,
        resource_provenance["observed_sample_interval_ms"],
    )
    resources = candidate["resources"]
    _compare(
        issues,
        f"{model_id}.probe.fixed_prompt_probe_peak_runner_rss_mib",
        round(peak_rss_bytes / 1024**2, 2),
        resources["fixed_prompt_probe_peak_runner_rss_mib"],
    )
    _compare(
        issues,
        f"{model_id}.probe.fixed_prompt_probe_peak_vram_mib",
        peak_vram_mib,
        resources["fixed_prompt_probe_peak_vram_mib"],
    )
    identity = candidate["model_identity"]
    digest = identity["ollama_tag_digest"]
    manifest_hash = identity["local_manifest_sha256"]
    if not isinstance(digest, str) or len(digest) != 64:
        issues.append(f"{model_id}.model_identity.ollama_tag_digest: expected 64 hex characters")
    _compare(
        issues,
        f"{model_id}.model_identity.local_manifest_sha256",
        manifest_hash,
        digest,
    )


def validate_baseline(baseline_path: Path) -> tuple[str, ...]:
    baseline_path = baseline_path.resolve()
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    issues: list[str] = []
    for candidate in baseline["llm_candidates"]:
        try:
            _validate_candidate(baseline_path, candidate, issues)
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            candidate_name = candidate.get("model_id", "<unknown>")
            issues.append(f"{candidate_name}: {type(error).__name__}: {error}")

    candidate_by_id = {
        candidate["model_id"]: candidate
        for candidate in baseline["llm_candidates"]
        if candidate["model_id"] in {DEFAULT_MODEL, QUALITY_MODEL}
    }
    try:
        selected = select_llm(
            tuple(
                CandidateMetric(
                    model=model_id,
                    p95_ttft_seconds=candidate["timing_seconds"]["p95_ttft"],
                    peak_vram_mb=candidate["resources"]["fixed_prompt_probe_peak_vram_mib"],
                    stable_30_minutes=candidate["stable_30_minutes"] is True,
                )
                for model_id, candidate in candidate_by_id.items()
            )
        )
        _compare(
            issues,
            "selection.selected_model",
            selected,
            baseline["selection"]["selected_model"],
        )
    except (KeyError, TypeError, ValueError) as error:
        issues.append(f"selection: {type(error).__name__}: {error}")
    return tuple(issues)
