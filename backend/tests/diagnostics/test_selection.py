import json
from pathlib import Path

import pytest

from voxagent.diagnostics.selection import (
    DEFAULT_MODEL,
    QUALITY_MODEL,
    CandidateMetric,
    select_llm,
)


def test_quality_model_wins_only_inside_all_limits():
    candidates = (
        CandidateMetric("qwen3:4b-instruct-2507-q4_K_M", 2.1, 4800, True),
        CandidateMetric("qwen3.5:4b", 2.6, 5300, True),
    )
    assert select_llm(candidates) == "qwen3.5:4b"


def test_default_model_wins_when_quality_exceeds_vram_limit():
    candidates = (
        CandidateMetric("qwen3:4b-instruct-2507-q4_K_M", 2.1, 4800, True),
        CandidateMetric("qwen3.5:4b", 2.6, 5500, True),
    )
    assert select_llm(candidates) == "qwen3:4b-instruct-2507-q4_K_M"


@pytest.mark.parametrize(
    ("ttft", "vram", "stable", "expected"),
    [
        (3.0, 5400, True, QUALITY_MODEL),
        (3.001, 5400, True, DEFAULT_MODEL),
        (3.0, 5401, True, DEFAULT_MODEL),
        (3.0, 5400, False, DEFAULT_MODEL),
    ],
)
def test_quality_threshold_boundaries(ttft, vram, stable, expected):
    candidates = (
        CandidateMetric(DEFAULT_MODEL, 1.0, 4000, True),
        CandidateMetric(QUALITY_MODEL, ttft, vram, stable),
    )

    assert select_llm(candidates) == expected


def test_missing_quality_model_falls_back_to_default():
    assert select_llm((CandidateMetric(DEFAULT_MODEL, 1.0, 4000, True),)) == DEFAULT_MODEL


def test_missing_default_model_is_rejected_when_quality_is_ineligible():
    with pytest.raises(ValueError, match="Missing required baseline candidate"):
        select_llm((CandidateMetric(QUALITY_MODEL, 3.1, 4000, True),))


def test_missing_default_model_is_rejected_when_quality_is_eligible():
    with pytest.raises(ValueError, match="Missing required baseline candidate"):
        select_llm((CandidateMetric(QUALITY_MODEL, 3.0, 5400, True),))


def test_duplicate_model_ids_are_rejected():
    with pytest.raises(ValueError, match="Duplicate candidate model"):
        select_llm(
            (
                CandidateMetric(DEFAULT_MODEL, 1.0, 4000, True),
                CandidateMetric(DEFAULT_MODEL, 2.0, 4500, True),
            )
        )


def test_committed_baseline_selects_recorded_model():
    repo_root = Path(__file__).resolve().parents[3]
    baseline = json.loads(
        (repo_root / "benchmarks" / "target-machine-baseline.json").read_text(encoding="utf-8")
    )
    candidates = tuple(
        CandidateMetric(
            model=item["model_id"],
            p95_ttft_seconds=item["timing_seconds"]["p95_ttft"],
            peak_vram_mb=item["resources"]["fixed_prompt_probe_peak_vram_mib"],
            stable_30_minutes=item["stable_30_minutes"] is True,
        )
        for item in baseline["llm_candidates"]
        if item["model_id"] in {DEFAULT_MODEL, QUALITY_MODEL}
    )

    assert select_llm(candidates) == baseline["selection"]["selected_model"]
