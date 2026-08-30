import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from voxagent.cli import app
from voxagent.diagnostics.baseline_validator import validate_baseline

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE = REPO_ROOT / "benchmarks" / "target-machine-baseline.json"


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
