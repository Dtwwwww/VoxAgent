import json
from pathlib import Path

from typer.testing import CliRunner

from voxagent.cli import app
from voxagent.diagnostics.ollama_runtime import (
    OllamaRuntimeExpectation,
    OllamaRuntimeObservation,
    OllamaRuntimeReport,
    evaluate_ollama_runtime,
)


def test_runtime_evaluation_accepts_loopback_matching_inventory_and_offline_env():
    digest = "a" * 64
    expectation = OllamaRuntimeExpectation(
        version="0.33.2",
        model_digests={"qwen3:4b": digest},
    )
    observation = OllamaRuntimeObservation(
        listener_addresses=("127.0.0.1", "::1"),
        api_version="0.33.2",
        api_model_digests={"qwen3:4b": digest},
        local_manifest_digests={"qwen3:4b": digest},
        server_environment={"OLLAMA_NO_CLOUD": "1"},
    )

    report = evaluate_ollama_runtime(expectation, observation)

    assert report.valid is True
    assert report.offline_status == "verified"
    assert report.issues == ()


def test_runtime_evaluation_blocks_non_loopback_mismatch_and_unverified_offline():
    expectation = OllamaRuntimeExpectation(
        version="0.33.2",
        model_digests={"qwen3:4b": "a" * 64},
    )
    observation = OllamaRuntimeObservation(
        listener_addresses=("0.0.0.0",),
        api_version="0.34.0",
        api_model_digests={"qwen3:4b": "b" * 64},
        local_manifest_digests={"qwen3:4b": "c" * 64},
        server_environment=None,
    )

    report = evaluate_ollama_runtime(expectation, observation)
    codes = {issue.code for issue in report.issues}

    assert report.valid is False
    assert report.offline_status == "unverified"
    assert codes == {
        "ollama_listener_not_loopback",
        "ollama_version_mismatch",
        "ollama_api_digest_mismatch",
        "ollama_local_manifest_mismatch",
        "ollama_offline_unverified",
    }


def test_verify_ollama_runtime_cli_returns_machine_readable_block(monkeypatch, tmp_path):
    report = OllamaRuntimeReport(
        valid=False,
        offline_status="unverified",
        issues=(),
        observation=OllamaRuntimeObservation(
            listener_addresses=(),
            api_version=None,
            api_model_digests={},
            local_manifest_digests={},
            server_environment=None,
            collection_errors=("API unavailable",),
        ),
    )
    monkeypatch.setattr("voxagent.cli.verify_ollama_runtime", lambda **_kwargs: report)

    result = CliRunner().invoke(
        app,
        [
            "verify-ollama-runtime",
            "--data-root",
            str(tmp_path),
            "--baseline",
            str(Path("baseline.json")),
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout)["valid"] is False
