import asyncio
import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

import aiosqlite
import httpx
import typer
import uvicorn
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from voxagent.agent.evaluation import evaluate_agent, load_dataset
from voxagent.agent.service import AgentService
from voxagent.api.app import _validate_session_token, create_app
from voxagent.api.data import LocalDataService
from voxagent.api.knowledge import LocalKnowledgeService
from voxagent.api.memory import LocalMemoryService
from voxagent.api.persona import LocalPersonaService
from voxagent.config import AppPaths, resolve_data_root
from voxagent.conversation.context import (
    ContextAssembler,
    LocalMemoryProposalService,
    MemoryProposalParser,
    SqliteContextSource,
)
from voxagent.conversation.orchestrator import ConversationOrchestrator
from voxagent.conversation.persistence import SqliteConversationStore
from voxagent.db.backup import DailyBackupManager
from voxagent.db.connection import open_database
from voxagent.db.migrations import migrate
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
    run_tts_benchmark,
    select_partial_asr_model,
    validate_fixture_checksum,
)
from voxagent.llm.ollama import OllamaClient
from voxagent.memory.embedder import BgeSmallZhEmbedder
from voxagent.speech.asr import (
    SenseVoiceAsr,
    SenseVoiceCandidatePauseAsr,
    StreamingParaformerAsr,
)
from voxagent.speech.endpoint import EndpointDetector
from voxagent.speech.model_manifest import SPEECH_MODELS
from voxagent.speech.tts import SherpaOfflineTts, prepare_voice_review
from voxagent.speech.vad import VadDetector
from voxagent.speech.voice_catalog import (
    VoiceCatalog,
    load_production_catalog,
)
from voxagent.speech.voice_selection import (
    VoiceSelectionError,
    publish_approved_voice_artifacts,
)
from voxagent.tools.app_launcher import WindowsAllowlistedLauncher
from voxagent.tools.builtin import build_builtin_registry
from voxagent.tools.confirmation import ConfirmationService
from voxagent.tools.repository import ToolRepository

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


@app.command("evaluate-agent")
def evaluate_agent_command(
    dataset: Annotated[Path, typer.Option("--dataset", dir_okay=False)],
    mode: Annotated[str, typer.Option("--mode")] = "fake",
    model: Annotated[str, typer.Option("--model")] = "qwen3:4b",
) -> None:
    if mode not in {"fake", "local"}:
        raise typer.BadParameter("--mode must be fake or local")
    try:
        cases = load_dataset(dataset.resolve())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise typer.BadParameter(
            f"invalid Agent dataset: {error}", param_hint="--dataset"
        ) from error

    async def execute():
        if mode == "fake":
            return await evaluate_agent(
                cases, mode="fake", model_name=model
            )
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:11434", trust_env=False
        ) as http:
            return await evaluate_agent(
                cases,
                mode="local",
                model_name=model,
                model=OllamaClient(http),
            )

    report = asyncio.run(execute())
    typer.echo(report.model_dump_json(indent=2))
    if report.passed != report.total:
        raise typer.Exit(code=1)


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


class _CatalogTts:
    def __init__(
        self,
        catalog: VoiceCatalog,
        engines: dict[str, SherpaOfflineTts],
    ) -> None:
        self._catalog = catalog
        self._engines = engines

    def synthesize(self, text: str, voice_key: str, speed: float):
        profile = self._catalog.get(voice_key)
        engine = self._engines.get(profile.engine)
        if engine is None:
            raise RuntimeError(
                "台湾腔女声已加入音色选择，但本机尚未配置 BreezyVoice 合成服务；"
                "请先完成本地模型配置"
            )
        return engine.synthesize(text, voice_key, speed)


def _create_production_app(session_token: str):
    _validate_session_token(session_token)
    catalog = load_production_catalog()
    root = resolve_data_root(None)
    paths = AppPaths.from_root(root)
    paths.create()
    try:
        baseline = json.loads(
            (REPO_ROOT / "benchmarks" / "target-machine-baseline.json").read_text(
                encoding="utf-8"
            )
        )
        model_id = baseline["selection"]["selected_model"]
        partial_model_id = baseline["asr_candidates"][0]["partial_model_id"]
    except (KeyError, IndexError, OSError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("Committed runtime model selection is missing or invalid") from error
    if not isinstance(model_id, str) or not model_id:
        raise RuntimeError("Committed runtime LLM selection is missing or invalid")
    if partial_model_id not in {
        "sensevoice-int8",
        "streaming-paraformer-bilingual-zh-en",
    }:
        raise RuntimeError("Committed partial ASR selection is missing or invalid")

    database_path = paths.data / "voxagent.db"
    database = open_database(database_path)
    migrate(database)
    mutation_lock = asyncio.Lock()
    embedder = BgeSmallZhEmbedder.from_path(
        paths.models / "embeddings" / "bge-small-zh-v1.5"
    )
    context_source = SqliteContextSource(database_path, embedder)
    persona_service = LocalPersonaService(database_path, mutation_lock)
    context_assembler = ContextAssembler(
        persona_service.load_config,
        context_source.search_memories,
        context_source.search_knowledge,
    )
    http = httpx.AsyncClient(base_url="http://127.0.0.1:11434", trust_env=False)
    ollama = OllamaClient(http)
    memory_proposer = LocalMemoryProposalService(ollama, MemoryProposalParser())
    knowledge_service = LocalKnowledgeService(
        database_path, paths.temp / "knowledge-uploads", embedder, mutation_lock
    )
    memory_service = LocalMemoryService(database_path, embedder, mutation_lock)
    data_service = LocalDataService(database_path, paths.data, mutation_lock)
    backup_manager = DailyBackupManager(paths.data / "backups")
    tool_repository = ToolRepository(database)
    tool_registry = build_builtin_registry(
        database,
        context_source,
        WindowsAllowlistedLauncher(),
    )
    agent_service: AgentService | None = None
    checkpoint_connection: aiosqlite.Connection | None = None

    def orchestrator_factory() -> ConversationOrchestrator:
        if agent_service is None:
            raise RuntimeError("Agent checkpoint runtime has not started")
        final_asr = SenseVoiceAsr.from_model_dir(
            _speech_model_directory(root, "sensevoice-int8")
        )
        if partial_model_id == "sensevoice-int8":
            partial_asr = SenseVoiceCandidatePauseAsr(
                SenseVoiceAsr.from_model_dir(
                    _speech_model_directory(root, "sensevoice-int8")
                )
            )
        else:
            partial_asr = StreamingParaformerAsr.from_model_dir(
                _speech_model_directory(
                    root, "streaming-paraformer-bilingual-zh-en"
                )
            )
        tts = _CatalogTts(
            catalog,
            {
                "kokoro": SherpaOfflineTts.from_model_dir(
                    _speech_model_directory(root, "kokoro-int8-zh-en"),
                    0,
                    engine="kokoro",
                    catalog=catalog,
                ),
            },
        )
        return ConversationOrchestrator(
            model_id=model_id,
            vad=VadDetector.from_model_path(
                _speech_model_directory(root, "silero-vad") / "silero_vad.onnx"
            ),
            endpoint=EndpointDetector("natural"),
            asr=final_asr,
            partial_asr=partial_asr,
            llm=ollama,
            tts=tts,
            voice_catalog=catalog,
            context_assembler=context_assembler,
            memory_proposer=memory_proposer,
            conversation_store=SqliteConversationStore(database_path, mutation_lock),
            agent_service=agent_service,
        )

    async def startup() -> None:
        nonlocal agent_service, checkpoint_connection
        checkpoint_connection = await aiosqlite.connect(
            paths.data / "agent-checkpoints.db"
        )
        checkpoint_saver = AsyncSqliteSaver(checkpoint_connection)
        await checkpoint_saver.setup()
        agent_service = AgentService(
            model_name=model_id,
            model=ollama,
            registry=tool_registry,
            repository=tool_repository,
            confirmation=ConfirmationService(tool_repository, tool_registry),
            checkpointer=checkpoint_saver,
            now_utc=lambda: datetime.now(UTC),
        )

    async def shutdown() -> None:
        await http.aclose()
        if checkpoint_connection is not None:
            await checkpoint_connection.close()
        try:
            backup_manager.create(database, date.today())
        finally:
            database.close()

    application = create_app(
        orchestrator_factory,
        session_token,
        on_startup=startup,
        on_shutdown=shutdown,
        knowledge_service=knowledge_service,
        memory_service=memory_service,
        persona_service=persona_service,
        data_service=data_service,
        tool_audit_reader=tool_repository,
    )
    application.state.ollama_http = http
    application.state.plan3_database = database
    return application


@app.command("serve")
def serve(
    session_token: Annotated[str, typer.Option("--session-token")],
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
) -> None:
    try:
        application = _create_production_app(session_token)
    except Exception as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=2) from error
    uvicorn.run(
        application,
        host="127.0.0.1",
        port=port,
        ws_max_size=64 * 1024,
        access_log=False,
    )


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
        typer.echo(f"Error: invalid --wav: {error}", err=True)
        raise typer.Exit(code=2) from error
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


@app.command("benchmark-tts")
def benchmark_tts(
    engine: Annotated[str, typer.Option("--engine")],
    voice_id: Annotated[int, typer.Option("--voice-id")],
    output: Annotated[Path, typer.Option("--output", dir_okay=False)],
    data_root: Annotated[Path | None, typer.Option("--data-root", dir_okay=True)] = None,
) -> None:
    model_names = {"kokoro": "kokoro-int8-zh-en", "melo": "melo-zh-en"}
    if engine not in model_names:
        raise typer.BadParameter("--engine must be kokoro or melo")
    if voice_id < 0:
        raise typer.BadParameter("--voice-id must be non-negative")
    root = resolve_data_root(data_root)
    tts = SherpaOfflineTts.from_model_dir(
        _speech_model_directory(root, model_names[engine]),
        voice_id,
        engine=engine,
    )
    report = run_tts_benchmark(tts, native_voice_id=voice_id)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@app.command("prepare-voice-review")
def prepare_voice_review_command(
    output_dir: Annotated[Path, typer.Option("--output-dir", file_okay=False)],
    data_root: Annotated[Path | None, typer.Option("--data-root", dir_okay=True)] = None,
) -> None:
    root = resolve_data_root(data_root)
    kokoro = SherpaOfflineTts.from_model_dir(
        _speech_model_directory(root, "kokoro-int8-zh-en"), 3, engine="kokoro"
    )
    melo = SherpaOfflineTts.from_model_dir(
        _speech_model_directory(root, "melo-zh-en"), 0, engine="melo"
    )
    template = prepare_voice_review(output_dir, kokoro=kokoro, melo=melo)
    typer.echo(json.dumps(template, ensure_ascii=False, indent=2))


@app.command("finalize-voice-review")
def finalize_voice_review_command(
    review_template: Annotated[Path, typer.Option("--review-template", dir_okay=False)],
    style_output: Annotated[Path, typer.Option("--style-output", dir_okay=False)],
    catalog_output: Annotated[Path, typer.Option("--catalog-output", dir_okay=False)],
) -> None:
    try:
        publish_approved_voice_artifacts(review_template, style_output, catalog_output)
    except (VoiceSelectionError, OSError, json.JSONDecodeError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=2) from error


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
