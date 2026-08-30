from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

from voxagent.diagnostics.llm_benchmark import LLM_BENCHMARK_PROMPTS
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
    benchmarks_root = baseline_path.parent.resolve()
    resolved = (benchmarks_root.parent / Path(recorded_path)).resolve()
    if not resolved.is_relative_to(benchmarks_root):
        raise ValueError(f"Artifact path resolves outside benchmarks: {recorded_path}")
    return resolved


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
    _compare(issues, f"{model_id}.timing.schema_version", timing.get("schema_version"), 1)
    _compare(issues, f"{model_id}.timing.model", timing["model"], model_id)
    _compare(
        issues,
        f"{model_id}.timing.artifact_sha256",
        _sha256(timing_path),
        timing_provenance["artifact_sha256"],
    )
    runs = timing["runs"]
    run_ids = [run.get("run_id") for run in runs]
    _compare(
        issues,
        f"{model_id}.timing.fixed_prompts",
        [run.get("prompt") for run in runs],
        list(LLM_BENCHMARK_PROMPTS),
    )
    _compare(issues, f"{model_id}.timing.run_ids", run_ids, ["run-1", "run-2", "run-3"])
    if any(not str(run.get("text", "")).strip() for run in runs):
        issues.append(f"{model_id}.timing.non_empty_text: every run must contain text")
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
    attestation_path = _artifact_path(baseline_path, identity["attestation_path"])
    attestation_hash = _sha256(attestation_path)
    _compare(
        issues,
        f"{model_id}.model_identity.attestation_sha256",
        attestation_hash,
        identity["attestation_sha256"],
    )
    _compare(
        issues,
        f"{model_id}.model_identity.attestation_matches_tag_digest",
        attestation_hash,
        digest,
    )
    manifest = json.loads(attestation_path.read_text(encoding="utf-8"))
    _compare(issues, f"{model_id}.model_identity.schemaVersion", manifest.get("schemaVersion"), 2)
    config = manifest.get("config") or {}
    layers = manifest.get("layers") or []
    if not config.get("digest") or not config.get("mediaType") or not layers:
        issues.append(f"{model_id}.model_identity.manifest: config and layers must be non-empty")
    digest_pattern = re.compile(r"sha256:[0-9a-f]{64}")
    references = [config.get("digest"), *(layer.get("digest") for layer in layers)]
    invalid_references = [
        reference
        for reference in references
        if not isinstance(reference, str) or not digest_pattern.fullmatch(reference)
    ]
    if invalid_references:
        issues.append(f"{model_id}.model_identity.blob_refs: every digest must be lowercase sha256")
    if not any(layer.get("mediaType") == "application/vnd.ollama.image.model" for layer in layers):
        issues.append(f"{model_id}.model_identity.layers: model layer is required")


def validate_baseline(baseline_path: Path) -> tuple[str, ...]:
    baseline_path = baseline_path.resolve()
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    issues: list[str] = []
    _compare(issues, "baseline.schema_version", baseline.get("schema_version"), 2)
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
    quality = candidate_by_id.get(QUALITY_MODEL)
    if quality is not None:
        try:
            soak_path = _artifact_path(baseline_path, quality["stability_evidence_path"])
            _compare(
                issues,
                "qwen3.5:4b.stability_evidence_sha256",
                _sha256(soak_path),
                quality["stability_evidence_sha256"],
            )
            soak = json.loads(soak_path.read_text(encoding="utf-8"))
            _compare(issues, "qwen3.5:4b.soak.model_id", soak.get("model_id"), QUALITY_MODEL)
            _compare(
                issues,
                "qwen3.5:4b.soak.stability_status",
                soak.get("stability_status"),
                quality["stability_status"],
            )
            _compare(issues, "qwen3.5:4b.soak.stopped_early", soak.get("stopped_early"), True)
            _compare(
                issues,
                "qwen3.5:4b.soak.raw_samples_retained",
                soak.get("raw_samples_retained"),
                False,
            )
            _compare(
                issues,
                "qwen3.5:4b.soak.stable_30_minutes",
                quality["stable_30_minutes"],
                soak.get("stability_status") == "passed" and not soak.get("stopped_early"),
            )
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            issues.append(f"qwen3.5:4b.soak: {type(error).__name__}: {error}")
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
        selection_inputs = baseline["selection"]["quality_model_inputs"]
        if quality is not None:
            _compare(
                issues,
                "selection.quality_model_inputs",
                selection_inputs,
                {
                    "p95_ttft_seconds": quality["timing_seconds"]["p95_ttft"],
                    "peak_vram_mb": quality["resources"]["fixed_prompt_probe_peak_vram_mib"],
                    "stable_30_minutes": quality["stable_30_minutes"],
                },
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
