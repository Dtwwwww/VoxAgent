import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import httpx
import typer

from voxagent.config import AppPaths, resolve_data_root
from voxagent.diagnostics.baseline_validator import validate_baseline
from voxagent.diagnostics.hardware import collect_hardware, evaluate_preflight
from voxagent.diagnostics.llm_benchmark import LLM_BENCHMARK_PROMPTS, run_llm_benchmark
from voxagent.diagnostics.ollama_runtime import verify_ollama_runtime
from voxagent.diagnostics.resource_probe import ProbeConfig, run_resource_probe
from voxagent.diagnostics.speech_benchmark import (
    FixtureChecksumError,
    prepare_asr_baseline_update,
    publish_asr_benchmark,
    run_asr_benchmark,
    run_partial_probe,
    select_partial_asr_model,
    validate_fixture_checksum,
)
from voxagent.llm.ollama import OllamaClient
from voxagent.speech.asr import (
    SenseVoiceAsr,
    SenseVoiceCandidatePauseAsr,
    StreamingParaformerAsr,
)
from voxagent.speech.model_manifest import SPEECH_MODELS

REPO_ROOT = Path(__file__).resolve().parents[3]

app = typer.Typer(no_args_is_help=True)

@app.callback()
def main() -> None:
    """Run VoxAgent local diagnostics."""


@app.command("paths")
def show_paths(
    data_root: Annotated[Path | None, typer.Option("--data-root", dir_okay=True)] = None,
) -> None:
    paths = AppPaths.from_root(resolve_data_root(data_root))
    paths.create()
    typer.echo(str(paths.root))


@app.command("preflight")
def preflight(
    data_root: Annotated[Path | None, typer.Option("--data-root", dir_okay=True)] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    paths = AppPaths.from_root(resolve_data_root(data_root))
    snapshot = collect_hardware(paths)
    issues = evaluate_preflight(snapshot)
    payload = {"hardware": snapshot.to_dict(), "issues": [asdict(issue) for issue in issues]}
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo("PASS" if not issues else "BLOCKED")
    if any(issue.blocking for issue in issues):
        raise typer.Exit(code=2)


@app.command("benchmark-llm")
def benchmark_llm(
    model: Annotated[str, typer.Option("--model")] = "",
    output: Annotated[Path, typer.Option("--output", dir_okay=False)] = Path("benchmark.json"),
) -> None:
    if not model.strip():
        raise typer.BadParameter("--model is required")

    async def execute() -> dict[str, object]:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:11434", trust_env=False
        ) as http:
            result = await run_llm_benchmark(OllamaClient(http), model, LLM_BENCHMARK_PROMPTS)
            return result.to_dict()

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(asyncio.run(execute()), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _speech_model_directory(data_root: Path, model_name: str) -> Path:
    model = next(item for item in SPEECH_MODELS if item.name == model_name)
    return (data_root / "models" / "speech" / model.directory_name).resolve()


@app.command("benchmark-asr")
def benchmark_asr(
    wav: Annotated[Path, typer.Option("--wav", dir_okay=False)],
    output: Annotated[Path, typer.Option("--output", dir_okay=False)],
    data_root: Annotated[Path | None, typer.Option("--data-root", dir_okay=True)] = None,
    baseline: Annotated[Path | None, typer.Option("--baseline", dir_okay=False)] = None,
) -> None:
    wav = wav.resolve()
    try:
        validate_fixture_checksum(wav, wav.parent / "checksums.json")
    except FixtureChecksumError as error:
        raise typer.BadParameter(str(error), param_hint="--wav") from error
    baseline_path = (
        baseline or REPO_ROOT / "benchmarks" / "target-machine-baseline.json"
    ).resolve()
    try:
        json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise typer.BadParameter(
            f"Invalid baseline: {baseline_path}", param_hint="--baseline"
        ) from error
    root = resolve_data_root(data_root)
    final_asr = SenseVoiceAsr.from_model_dir(_speech_model_directory(root, "sensevoice-int8"))
    candidate_asr = SenseVoiceAsr.from_model_dir(_speech_model_directory(root, "sensevoice-int8"))
    candidate_partial = SenseVoiceCandidatePauseAsr(candidate_asr)
    _, candidate_p95 = run_partial_probe(
        wav,
        wav.parent / "checksums.json",
        partial_asr=candidate_partial,
    )
    uses_sensevoice_partials = (
        select_partial_asr_model(candidate_p95) == "sensevoice-int8"
    )
    if uses_sensevoice_partials:
        partial_asr = candidate_partial
    else:
        partial_asr = StreamingParaformerAsr.from_model_dir(
            _speech_model_directory(root, "streaming-paraformer-bilingual-zh-en")
        )
    report = run_asr_benchmark(
        wav,
        wav.parent / "checksums.json",
        final_asr=final_asr,
        partial_asr=partial_asr,
    )
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    artifact_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    try:
        baseline_text = prepare_asr_baseline_update(
            baseline_path, output, report, artifact_text.encode("utf-8")
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise typer.BadParameter(
            f"Invalid baseline: {baseline_path}", param_hint="--baseline"
        ) from error
    publish_asr_benchmark(
        output,
        artifact_text.encode("utf-8"),
        baseline_path,
        baseline_text.encode("utf-8"),
    )


@app.command("validate-baseline")
def validate_baseline_command(
    baseline: Annotated[Path, typer.Option("--baseline", dir_okay=False)] = Path(
        "benchmarks/target-machine-baseline.json"
    ),
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    issues = validate_baseline(baseline)
    payload = {"valid": not issues, "issues": list(issues)}
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2) if as_json else payload)
    if issues:
        raise typer.Exit(code=1)


@app.command("verify-ollama-runtime")
def verify_ollama_runtime_command(
    data_root: Annotated[Path | None, typer.Option("--data-root", dir_okay=True)] = None,
    baseline: Annotated[Path, typer.Option("--baseline", dir_okay=False)] = Path(
        "benchmarks/target-machine-baseline.json"
    ),
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    report = verify_ollama_runtime(
        data_root=resolve_data_root(data_root),
        baseline_path=baseline.resolve(),
    )
    payload = report.to_dict()
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2) if as_json else payload)
    if not report.valid:
        raise typer.Exit(code=2)


@app.command("probe-resources")
def probe_resources(
    model: Annotated[str, typer.Option("--model")],
    output: Annotated[Path, typer.Option("--output", dir_okay=False)],
    manifest_sha256: Annotated[str, typer.Option("--manifest-sha256")],
    model_blob_digest: Annotated[str, typer.Option("--model-blob-digest")],
    runner_pid: Annotated[int | None, typer.Option("--runner-pid")] = None,
    mode: Annotated[str, typer.Option("--mode")] = "observe",
    duration_seconds: Annotated[float, typer.Option("--duration-seconds")] = 60,
    sample_interval_ms: Annotated[int, typer.Option("--sample-interval-ms")] = 100,
) -> None:
    if mode not in {"observe", "soak"}:
        raise typer.BadParameter("--mode must be observe or soak")
    config = ProbeConfig(
        model_id=model,
        mode=mode,
        duration_seconds=duration_seconds,
        sample_interval_ms=sample_interval_ms,
        stop_available_ram_gib=0.5,
        stop_runner_rss_mib=7000,
        model_manifest_sha256=manifest_sha256,
        model_blob_digest=model_blob_digest,
        runner_pid=runner_pid,
    )
    workload = None
    if mode == "soak":
        client = httpx.Client(base_url="http://127.0.0.1:11434", trust_env=False, timeout=120)

        def workload() -> None:
            response = client.post(
                "/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": LLM_BENCHMARK_PROMPTS[0]}],
                    "stream": False,
                    "think": False,
                    "options": {"num_ctx": 8192},
                },
            )
            response.raise_for_status()

    try:
        report = run_resource_probe(config, workload=workload)
    finally:
        if mode == "soak":
            client.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    if report.sampler_error is not None:
        raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
