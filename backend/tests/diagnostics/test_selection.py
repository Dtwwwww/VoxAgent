from voxagent.diagnostics.selection import CandidateMetric, select_llm


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
