from dataclasses import dataclass

DEFAULT_MODEL = "qwen3:4b-instruct-2507-q4_K_M"
QUALITY_MODEL = "qwen3.5:4b"


@dataclass(frozen=True, slots=True)
class CandidateMetric:
    model: str
    p95_ttft_seconds: float
    peak_vram_mb: int
    stable_30_minutes: bool


def select_llm(candidates: tuple[CandidateMetric, ...]) -> str:
    model_ids = [candidate.model for candidate in candidates]
    if len(model_ids) != len(set(model_ids)):
        raise ValueError("Duplicate candidate model IDs are not allowed")
    indexed = {candidate.model: candidate for candidate in candidates}
    if DEFAULT_MODEL not in indexed:
        raise ValueError(f"Missing required baseline candidate: {DEFAULT_MODEL}")
    quality = indexed.get(QUALITY_MODEL)
    if (
        quality is not None
        and quality.p95_ttft_seconds <= 3.0
        and quality.peak_vram_mb <= 5400
        and quality.stable_30_minutes
    ):
        return QUALITY_MODEL
    return DEFAULT_MODEL
