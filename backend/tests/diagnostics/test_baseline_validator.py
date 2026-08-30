import hashlib
import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from voxagent.cli import app
from voxagent.diagnostics.baseline_validator import validate_baseline

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE = REPO_ROOT / "benchmarks" / "target-machine-baseline.json"


def _rewrite_soak_and_rehash_baseline(copied: Path, soak: dict[str, object]) -> None:
    soak_path = copied / "qwen3.5-soak-summary.json"
    soak_path.write_text(json.dumps(soak), encoding="utf-8")
    baseline_path = copied / "target-machine-baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    quality = next(
        candidate
        for candidate in baseline["llm_candidates"]
        if candidate["model_id"] == "qwen3.5:4b"
    )
    quality["stability_evidence_sha256"] = hashlib.sha256(soak_path.read_bytes()).hexdigest()
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")


def test_committed_baseline_is_fully_recomputable():
    assert validate_baseline(BASELINE) == ()


def test_validator_reports_tampered_timing_artifact(tmp_path):
    copied = tmp_path / "repo" / "benchmarks"
    shutil.copytree(REPO_ROOT / "benchmarks", copied)
    timing_path = copied / "qwen3-4b.json"
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    timing["runs"][0]["ttft_seconds"] = 99
    timing_path.write_text(json.dumps(timing), encoding="utf-8")

    issues = validate_baseline(copied / "target-machine-baseline.json")

    assert any("artifact_sha256" in issue for issue in issues)
    assert any("p95_ttft" in issue for issue in issues)


def test_validate_baseline_cli_returns_nonzero_json_for_mismatch(tmp_path):
    copied = tmp_path / "repo" / "benchmarks"
    shutil.copytree(REPO_ROOT / "benchmarks", copied)
    baseline_path = copied / "target-machine-baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline["selection"]["selected_model"] = "wrong-model"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["validate-baseline", "--baseline", str(baseline_path), "--json"],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    assert payload["valid"] is False
    assert any("selected_model" in issue for issue in payload["issues"])


def test_validator_rejects_artifact_path_outside_benchmarks(tmp_path):
    copied = tmp_path / "repo" / "benchmarks"
    shutil.copytree(REPO_ROOT / "benchmarks", copied)
    baseline_path = copied / "target-machine-baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline["llm_candidates"][0]["timing_provenance"]["artifact_path"] = "../outside.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    issues = validate_baseline(baseline_path)

    assert any("outside benchmarks" in issue for issue in issues)


def test_validator_rejects_tampered_manifest_attestation(tmp_path):
    copied = tmp_path / "repo" / "benchmarks"
    shutil.copytree(REPO_ROOT / "benchmarks", copied)
    attestation = copied / "ollama-manifest-qwen3-4b.json"
    manifest = json.loads(attestation.read_text(encoding="utf-8"))
    manifest["layers"][0]["digest"] = "sha256:" + "0" * 64
    attestation.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")

    issues = validate_baseline(copied / "target-machine-baseline.json")

    assert any("model_identity" in issue for issue in issues)


def test_validator_rejects_incomplete_fixed_prompt_run(tmp_path):
    copied = tmp_path / "repo" / "benchmarks"
    shutil.copytree(REPO_ROOT / "benchmarks", copied)
    timing_path = copied / "qwen3-4b.json"
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    timing["runs"][0]["prompt"] = "unexpected prompt"
    timing["runs"][0]["text"] = ""
    timing_path.write_text(json.dumps(timing), encoding="utf-8")

    issues = validate_baseline(copied / "target-machine-baseline.json")

    assert any("fixed_prompts" in issue for issue in issues)
    assert any("non_empty_text" in issue for issue in issues)


def test_validator_rejects_soak_summary_inconsistent_with_candidate(tmp_path):
    copied = tmp_path / "repo" / "benchmarks"
    shutil.copytree(REPO_ROOT / "benchmarks", copied)
    soak_path = copied / "qwen3.5-soak-summary.json"
    soak = json.loads(soak_path.read_text(encoding="utf-8"))
    soak["stability_status"] = "passed"
    soak["raw_samples_retained"] = True
    soak_path.write_text(json.dumps(soak), encoding="utf-8")

    issues = validate_baseline(copied / "target-machine-baseline.json")

    assert any("stability_status" in issue for issue in issues)
    assert any("raw_samples_retained" in issue for issue in issues)


def test_validator_accepts_canonical_strict_target_probe(tmp_path):
    copied = tmp_path / "repo" / "benchmarks"
    shutil.copytree(REPO_ROOT / "benchmarks", copied)
    probe_path = copied / "resource-probe-qwen3-4b.json"
    probe = json.loads(probe_path.read_text(encoding="utf-8"))
    probe["target_identity"].update(
        {
            "runner_pid": 123,
            "attribution": "strict_target_pid",
        }
    )
    probe["target_identity"].pop("attribution_limitation", None)
    probe["schema_version"] = 3
    probe["gpu_attribution"] = "target_pid"
    for sample in probe["samples"]:
        if not sample["runner_rss_bytes"]:
            sample["runner_rss_bytes"] = [0]
        sample["vram_attribution"] = "target_pid"
    probe_path.write_text(json.dumps(probe), encoding="utf-8")

    baseline_path = copied / "target-machine-baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline["llm_candidates"][0]["resource_provenance"]["probe_sha256"] = (
        hashlib.sha256(probe_path.read_bytes()).hexdigest()
    )
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    assert validate_baseline(baseline_path) == ()


def test_validator_rejects_wrong_retained_soak_source_hash(tmp_path):
    copied = tmp_path / "repo" / "benchmarks"
    shutil.copytree(REPO_ROOT / "benchmarks", copied)
    soak = json.loads((copied / "qwen3.5-soak-summary.json").read_text(encoding="utf-8"))
    soak["retained_source_files"][0]["sha256"] = "0" * 64
    _rewrite_soak_and_rehash_baseline(copied, soak)

    issues = validate_baseline(copied / "target-machine-baseline.json")

    assert any("retained_source_files[0].sha256" in issue for issue in issues)


def test_validator_rejects_retained_soak_source_outside_benchmarks(tmp_path):
    copied = tmp_path / "repo" / "benchmarks"
    shutil.copytree(REPO_ROOT / "benchmarks", copied)
    soak = json.loads((copied / "qwen3.5-soak-summary.json").read_text(encoding="utf-8"))
    soak["retained_source_files"][0]["path"] = "../outside.json"
    _rewrite_soak_and_rehash_baseline(copied, soak)

    issues = validate_baseline(copied / "target-machine-baseline.json")

    assert any(
        "retained_source_files[0]" in issue and "outside benchmarks" in issue
        for issue in issues
    )


def test_validator_rejects_tampered_retained_soak_source(tmp_path):
    copied = tmp_path / "repo" / "benchmarks"
    shutil.copytree(REPO_ROOT / "benchmarks", copied)
    retained = copied / "resource-probe-qwen3.5-4b.json"
    retained.write_bytes(retained.read_bytes() + b"tampered")

    issues = validate_baseline(copied / "target-machine-baseline.json")

    assert any("retained_source_files[0].sha256" in issue for issue in issues)


def test_validator_rejects_timing_not_derived_by_declared_normalization(tmp_path):
    copied = tmp_path / "repo" / "benchmarks"
    shutil.copytree(REPO_ROOT / "benchmarks", copied)
    timing_path = copied / "qwen3.5-4b.json"
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    timing["runs"][0]["text"] += "changed after normalization"
    timing_path.write_text(json.dumps(timing), encoding="utf-8")
    baseline_path = copied / "target-machine-baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    quality = next(
        candidate
        for candidate in baseline["llm_candidates"]
        if candidate["model_id"] == "qwen3.5:4b"
    )
    quality["timing_provenance"]["artifact_sha256"] = hashlib.sha256(
        timing_path.read_bytes()
    ).hexdigest()
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    issues = validate_baseline(baseline_path)

    assert any("normalization_attestation" in issue for issue in issues)
