import asyncio
import base64
import json

import pytest
from typer.testing import CliRunner

from voxagent import cli
from voxagent.diagnostics.hardware import DiskSnapshot, HardwareSnapshot
from voxagent.speech.voice_catalog import VoiceCatalogError

SESSION_TOKEN = base64.urlsafe_b64encode(b"x" * 32).decode("ascii").rstrip("=")


def _valid_voice_review_template() -> dict[str, object]:
    return {
        "schema_version": 1,
        "seed": 20260830,
        "engine_comparison": [
            {
                "sample_id": f"engine-{index:03d}",
                "file": f"engine-{index:03d}.wav",
                "naturalness": None,
                "intelligibility": None,
            }
            for index in range(1, 7)
        ],
        "voice_style": [
            {
                "sample_id": f"voice-{index:03d}",
                "file": f"voice-{index:03d}.wav",
                "assigned_label": None,
                "naturalness": None,
                "intelligibility": None,
            }
            for index in range(1, 9)
        ],
        "required_voice_labels": ["清澈女声", "温柔女声", "沉稳男声", "阳光男声"],
        "scoring_rules": {
            "range": [1, 5],
            "minimum_selected_score": 3,
            "no_duplicate_assignments": True,
        },
    }


def test_preflight_outputs_json_and_blocks_when_gpu_is_unavailable(monkeypatch, tmp_path):
    snapshot = HardwareSnapshot(
        cpu_name="CPU",
        cpu_cores=4,
        cpu_threads=8,
        ram_total_gb=16,
        ram_free_gb=8,
        gpu=None,
        disks=(DiskSnapshot("C:\\", 200, 20), DiskSnapshot("D:\\", 557, 100)),
        selected_data_drive="D:\\",
        data_drive_probe_error=None,
    )
    monkeypatch.setattr(cli, "collect_hardware", lambda _: snapshot)

    result = CliRunner().invoke(cli.app, ["preflight", "--json", "--data-root", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 2
    assert payload["hardware"]["gpu"] is None
    assert {issue["code"] for issue in payload["issues"]} == {"vram_unsupported"}


def test_preflight_unavailable_selected_drive_is_machine_readable(monkeypatch):
    from types import SimpleNamespace

    from voxagent.diagnostics import hardware
    from voxagent.diagnostics.hardware import GpuSnapshot

    monkeypatch.setattr(hardware, "_cpu_name", lambda: "CPU")
    monkeypatch.setattr(
        hardware,
        "_gpu_snapshot",
        lambda: GpuSnapshot("GPU", 6144, 5000, "driver", "7.5"),
    )
    monkeypatch.setattr(
        hardware.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(total=16 * 1024**3, available=8 * 1024**3),
    )
    monkeypatch.setattr(hardware.psutil, "cpu_count", lambda logical: 8 if logical else 4)

    def probe_disk(drive: str) -> DiskSnapshot:
        if drive == "Z:\\":
            raise PermissionError("drive unavailable")
        return DiskSnapshot(drive, 200, 20)

    monkeypatch.setattr(hardware, "_disk", probe_disk)
    monkeypatch.setattr(cli, "collect_hardware", hardware.collect_hardware)

    result = CliRunner().invoke(
        cli.app,
        ["preflight", "--json", "--data-root", r"Z:\VoxAgentData"],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 2
    assert payload["hardware"]["selected_data_drive"] == "Z:\\"
    assert payload["hardware"]["data_drive_probe_error"] == "drive unavailable"
    assert [issue["code"] for issue in payload["issues"]] == ["data_drive_unavailable"]


def test_benchmark_llm_requires_model():
    result = CliRunner().invoke(cli.app, ["benchmark-llm"])

    assert result.exit_code != 0
    assert "--model is required" in result.stderr


def test_benchmark_llm_disables_environment_proxy(monkeypatch, tmp_path):
    created_client: dict[str, object] = {}

    class FakeHttpClient:
        def __init__(self, **kwargs):
            created_client.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_value, traceback):
            return False

    class FakeBenchmark:
        def to_dict(self):
            return {"model": "test-model", "runs": []}

    async def fake_run_llm_benchmark(client, model, prompts):
        return FakeBenchmark()

    monkeypatch.setattr(cli.httpx, "AsyncClient", FakeHttpClient)
    monkeypatch.setattr(cli, "run_llm_benchmark", fake_run_llm_benchmark)

    output = tmp_path / "benchmark.json"
    result = CliRunner().invoke(
        cli.app,
        ["benchmark-llm", "--model", "test-model", "--output", str(output)],
    )

    assert result.exit_code == 0
    assert created_client["base_url"] == "http://127.0.0.1:11434"
    assert created_client["trust_env"] is False
    assert output.exists()
    assert json.loads(output.read_text(encoding="utf-8")) == {"model": "test-model", "runs": []}


def test_benchmark_asr_rejects_an_unverified_fixture_before_model_loading(monkeypatch, tmp_path):
    wav = tmp_path / "mandarin-command.wav"
    wav.write_bytes(b"not a verified fixture")
    output = tmp_path / "result.json"
    loaded: list[object] = []
    monkeypatch.setattr(cli, "SenseVoiceAsr", lambda *args: loaded.append(args))

    result = CliRunner().invoke(
        cli.app,
        ["benchmark-asr", "--wav", str(wav), "--output", str(output)],
    )

    assert result.exit_code != 0
    assert "checksums.json" in result.stderr
    assert loaded == []
    assert not output.exists()


def test_benchmark_asr_anchors_its_default_baseline_at_repo_root(monkeypatch, tmp_path):
    fixture = tmp_path / "fixture" / "mandarin-command.wav"
    fixture.parent.mkdir()
    fixture.write_bytes(b"verified fixture")
    (fixture.parent / "checksums.json").write_text(
        '{"mandarin-command.wav": "'
        + __import__("hashlib").sha256(fixture.read_bytes()).hexdigest()
        + '"}',
        encoding="utf-8",
    )
    repo_root = tmp_path / "repo"
    baseline = repo_root / "benchmarks" / "target-machine-baseline.json"
    baseline.parent.mkdir(parents=True)
    baseline.write_text('{"asr_candidates": []}', encoding="utf-8")
    output = repo_root / "benchmarks" / "sensevoice-int8.json"

    class FakeSenseVoice:
        @classmethod
        def from_model_dir(cls, _):
            return object()

    monkeypatch.setattr(cli, "REPO_ROOT", repo_root)
    monkeypatch.setattr(cli, "SenseVoiceAsr", FakeSenseVoice)
    monkeypatch.setattr(cli, "SenseVoiceCandidatePauseAsr", lambda _: object())
    monkeypatch.setattr(cli, "_speech_model_directory", lambda *_: tmp_path)
    monkeypatch.setattr(cli, "run_partial_probe", lambda *args, **kwargs: ("sensevoice-int8", 0.1))
    monkeypatch.setattr(
        cli,
        "run_asr_benchmark",
        lambda *args, **kwargs: {
            "model_id": "sensevoice-int8",
            "run_count": 5,
            "transcript": "打开音乐",
        },
    )
    monkeypatch.chdir(tmp_path / "fixture")

    result = CliRunner().invoke(
        cli.app,
        ["benchmark-asr", "--wav", str(fixture), "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert output.is_file()
    candidate = json.loads(baseline.read_text(encoding="utf-8"))["asr_candidates"][0]
    assert candidate["artifact_sha256"] == __import__("hashlib").sha256(
        output.read_bytes()
    ).hexdigest()


def test_benchmark_asr_validates_baseline_before_writing_artifact(monkeypatch, tmp_path):
    fixture = tmp_path / "mandarin-command.wav"
    fixture.write_bytes(b"verified fixture")
    (tmp_path / "checksums.json").write_text(
        '{"mandarin-command.wav": "'
        + __import__("hashlib").sha256(fixture.read_bytes()).hexdigest()
        + '"}',
        encoding="utf-8",
    )
    output = tmp_path / "artifact.json"
    monkeypatch.setattr(cli, "run_partial_probe", lambda *args, **kwargs: ("sensevoice-int8", 0.1))

    result = CliRunner().invoke(
        cli.app,
        [
            "benchmark-asr",
            "--wav",
            str(fixture),
            "--output",
            str(output),
            "--baseline",
            str(tmp_path / "missing" / "baseline.json"),
        ],
    )

    assert result.exit_code != 0
    assert not output.exists()


@pytest.mark.parametrize(
    ("candidate_p95", "expected_partial"),
    [(0.3, "sensevoice"), (0.3004, "paraformer")],
)
def test_benchmark_asr_runs_one_final_benchmark_after_partial_selection(
    monkeypatch, tmp_path, candidate_p95, expected_partial
):
    import hashlib

    fixture = tmp_path / "mandarin-command.wav"
    fixture.write_bytes(b"verified fixture")
    (tmp_path / "checksums.json").write_text(
        '{"mandarin-command.wav": "' + hashlib.sha256(fixture.read_bytes()).hexdigest() + '"}',
        encoding="utf-8",
    )
    baseline = tmp_path / "benchmarks" / "target-machine-baseline.json"
    baseline.parent.mkdir()
    baseline.write_text('{"asr_candidates": []}', encoding="utf-8")
    output = tmp_path / "benchmarks" / "result.json"
    final = object()
    candidate = object()
    paraformer = object()
    created: list[object] = [final, candidate]
    benchmark_calls: list[tuple[object, object]] = []

    class FakeSenseVoice:
        @classmethod
        def from_model_dir(cls, _):
            return created.pop(0)

    class FakeParaformer:
        @classmethod
        def from_model_dir(cls, _):
            return paraformer

    monkeypatch.setattr(cli, "SenseVoiceAsr", FakeSenseVoice)
    monkeypatch.setattr(cli, "StreamingParaformerAsr", FakeParaformer)
    monkeypatch.setattr(cli, "SenseVoiceCandidatePauseAsr", lambda value: ("candidate", value))
    monkeypatch.setattr(cli, "_speech_model_directory", lambda *_: tmp_path)
    monkeypatch.setattr(
        cli,
        "run_partial_probe",
        lambda *args, **kwargs: ("sensevoice-int8", candidate_p95),
    )

    def fake_benchmark(*args, **kwargs):
        benchmark_calls.append((kwargs["final_asr"], kwargs["partial_asr"]))
        return {"model_id": "sensevoice-int8", "run_count": 5, "transcript": "打开音乐"}

    monkeypatch.setattr(cli, "run_asr_benchmark", fake_benchmark)

    result = CliRunner().invoke(
        cli.app,
        [
            "benchmark-asr",
            "--wav",
            str(fixture),
            "--output",
            str(output),
            "--baseline",
            str(baseline),
        ],
    )

    assert result.exit_code == 0, result.output
    assert benchmark_calls == [
        (final, ("candidate", candidate) if expected_partial == "sensevoice" else paraformer)
    ]


def test_benchmark_tts_loads_requested_engine_and_writes_report(monkeypatch, tmp_path):
    from voxagent import cli

    calls: list[tuple[object, ...]] = []

    class FakeTts:
        engine = "kokoro"

    def fake_from_model_dir(path, voice_id, *, engine, catalog=None):
        calls.append((path, voice_id, engine, catalog))
        return FakeTts()

    monkeypatch.setattr(cli.SherpaOfflineTts, "from_model_dir", fake_from_model_dir)
    monkeypatch.setattr(
        cli,
        "run_tts_benchmark",
        lambda tts, native_voice_id: {
            "engine": tts.engine,
            "native_voice_id": native_voice_id,
            "run_count": 5,
        },
    )
    output = tmp_path / "kokoro.json"

    result = CliRunner().invoke(
        cli.app,
        ["benchmark-tts", "--engine", "kokoro", "--voice-id", "3", "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert calls[0][1:3] == (3, "kokoro")
    assert json.loads(output.read_text(encoding="utf-8"))["run_count"] == 5


def test_prepare_voice_review_uses_local_engines_without_a_catalog(monkeypatch, tmp_path):
    from voxagent import cli

    created: list[tuple[str, object]] = []

    def fake_from_model_dir(path, voice_id, *, engine, catalog=None):
        created.append((engine, catalog))
        return object()

    monkeypatch.setattr(cli.SherpaOfflineTts, "from_model_dir", fake_from_model_dir)
    monkeypatch.setattr(
        cli,
        "prepare_voice_review",
        lambda output_dir, *, kokoro, melo: {"review_dir": str(output_dir)},
    )

    result = CliRunner().invoke(cli.app, ["prepare-voice-review", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert created == [("kokoro", None), ("melo", None)]


def test_finalize_voice_review_publishes_validated_style_and_catalog_together(tmp_path):
    review_template = tmp_path / "review-template.json"
    review_template.write_text(
        json.dumps(_valid_voice_review_template(), ensure_ascii=False),
        encoding="utf-8",
    )
    style_output = tmp_path / "tts-voice-style.json"
    catalog_output = tmp_path / "voice_catalog.json"

    result = CliRunner().invoke(
        cli.app,
        [
            "finalize-voice-review",
            "--review-template",
            str(review_template),
            "--style-output",
            str(style_output),
            "--catalog-output",
            str(catalog_output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(style_output.read_text(encoding="utf-8"))["selected_sample_id"] == (
        "voice-005"
    )
    assert json.loads(catalog_output.read_text(encoding="utf-8"))["voices"] == [
        {
            "voice_key": "default_voice",
            "display_name": "声灵默认音色",
            "description": "自然清晰，适合日常对话",
            "gender": "neutral",
            "engine": "kokoro",
            "native_voice_id": 3,
            "is_default": True,
            "previewable": True,
        }
    ]
    assert style_output.read_bytes().endswith(b"\n")
    assert catalog_output.read_bytes().endswith(b"\n")


def test_finalize_voice_review_rejects_invalid_input_without_partial_artifacts(tmp_path):
    review_template = tmp_path / "review-template.json"
    template = _valid_voice_review_template()
    template["seed"] = 7
    review_template.write_text(json.dumps(template), encoding="utf-8")
    style_output = tmp_path / "tts-voice-style.json"
    catalog_output = tmp_path / "voice_catalog.json"

    result = CliRunner().invoke(
        cli.app,
        [
            "finalize-voice-review",
            "--review-template",
            str(review_template),
            "--style-output",
            str(style_output),
            "--catalog-output",
            str(catalog_output),
        ],
    )

    assert result.exit_code != 0
    assert "Error:" in result.stderr
    assert not style_output.exists()
    assert not catalog_output.exists()


def test_serve_uses_localhost_message_limit_and_no_access_log(monkeypatch):
    application = object()
    created: list[str] = []
    run_calls: list[tuple[object, dict[str, object]]] = []
    monkeypatch.setattr(
        cli,
        "_create_production_app",
        lambda token: created.append(token) or application,
    )
    monkeypatch.setattr(
        cli.uvicorn,
        "run",
        lambda app, **kwargs: run_calls.append((app, kwargs)),
    )

    result = CliRunner().invoke(
        cli.app,
        ["serve", "--port", "8765", "--session-token", SESSION_TOKEN],
    )

    assert result.exit_code == 0, result.output
    assert created == [SESSION_TOKEN]
    assert run_calls == [
        (
            application,
            {
                "host": "127.0.0.1",
                "port": 8765,
                "ws_max_size": 64 * 1024,
                "access_log": False,
            },
        )
    ]


def test_serve_fails_clearly_when_blind_scored_voice_catalog_is_absent(monkeypatch):
    started: list[object] = []

    def missing_catalog(_token):
        raise VoiceCatalogError(
            "Production voice catalog requires scored blind voice review"
        )

    monkeypatch.setattr(cli, "_create_production_app", missing_catalog)
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: started.append(args))

    result = CliRunner().invoke(
        cli.app,
        ["serve", "--port", "8765", "--session-token", SESSION_TOKEN],
    )

    assert result.exit_code != 0
    assert "scored blind voice review" in result.stderr
    assert started == []


def test_production_app_rejects_bad_token_before_loading_private_assets(monkeypatch):
    catalog_loads: list[object] = []
    monkeypatch.setattr(
        cli, "load_production_catalog", lambda: catalog_loads.append(object())
    )

    with pytest.raises(ValueError, match="32-byte URL-safe"):
        cli._create_production_app("short")

    assert catalog_loads == []


def test_production_app_wires_local_knowledge_and_retrieval_context(monkeypatch, tmp_path):
    baseline = tmp_path / "benchmarks" / "target-machine-baseline.json"
    baseline.parent.mkdir()
    baseline.write_text(
        json.dumps(
            {
                "selection": {"selected_model": "qwen-local"},
                "asr_candidates": [{"partial_model_id": "sensevoice-int8"}],
            }
        ),
        encoding="utf-8",
    )
    database = type("Database", (), {"close": lambda self: setattr(self, "closed", True)})()
    database.closed = False
    embedder = object()
    source = type(
        "Source",
        (),
        {"search_memories": lambda *_: (), "search_knowledge": lambda *_: ()},
    )()
    assembler = object()
    proposer = object()
    knowledge = object()
    memory = object()
    data_service = object()
    conversation_store = object()
    backup_calls: list[tuple[object, object]] = []
    captured: dict[str, object] = {}

    class FakePersonaService:
        def load_config(self):
            return object()

    persona_service = FakePersonaService()

    class FakeHttp:
        closed = False

        async def aclose(self):
            self.closed = True

    fake_http = FakeHttp()
    tts_calls: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli, "resolve_data_root", lambda _: tmp_path)
    monkeypatch.setattr(cli, "load_production_catalog", lambda: object())
    def fake_open_database(path):
        captured["db_path"] = path
        return database

    def fake_load_embedder(path):
        captured["model_path"] = path
        return embedder

    monkeypatch.setattr(cli, "open_database", fake_open_database)
    monkeypatch.setattr(
        cli,
        "migrate",
        lambda connection: captured.setdefault("migrated", connection),
    )
    monkeypatch.setattr(cli.BgeSmallZhEmbedder, "from_path", fake_load_embedder)
    monkeypatch.setattr(cli, "SqliteContextSource", lambda database_path, model: source)
    monkeypatch.setattr(cli, "ContextAssembler", lambda persona, memories, documents: assembler)
    monkeypatch.setattr(cli, "LocalMemoryProposalService", lambda client, parser: proposer)
    gates: list[object] = []
    monkeypatch.setattr(
        cli,
        "LocalKnowledgeService",
        lambda database_path, temp, model, gate: (gates.append(gate), knowledge)[1],
    )
    monkeypatch.setattr(
        cli,
        "LocalMemoryService",
        lambda database_path, model, gate: (gates.append(gate), memory)[1],
    )
    monkeypatch.setattr(
        cli,
        "LocalPersonaService",
        lambda database_path, gate: (gates.append(gate), persona_service)[1],
    )
    monkeypatch.setattr(
        cli,
        "LocalDataService",
        lambda database_path, data, gate: (gates.append(gate), data_service)[1],
    )
    monkeypatch.setattr(
        cli,
        "SqliteConversationStore",
        lambda database_path, gate: (gates.append(gate), conversation_store)[1],
    )
    monkeypatch.setattr(
        cli,
        "DailyBackupManager",
        lambda directory: type(
            "Backup",
            (),
            {"create": lambda self, connection, day: backup_calls.append((connection, day))},
        )(),
    )
    monkeypatch.setattr(cli.httpx, "AsyncClient", lambda **_: fake_http)
    monkeypatch.setattr(
        cli.SherpaOfflineTts,
        "from_model_dir",
        lambda _path, _voice_id, *, engine, catalog: (
            tts_calls.append({"engine": engine, "catalog": catalog}) or object()
        ),
    )
    monkeypatch.setattr(cli.SenseVoiceAsr, "from_model_dir", lambda _: object())
    monkeypatch.setattr(cli, "SenseVoiceCandidatePauseAsr", lambda asr: asr)
    monkeypatch.setattr(cli.VadDetector, "from_model_path", lambda _: object())
    monkeypatch.setattr(cli, "EndpointDetector", lambda _: object())
    monkeypatch.setattr(cli, "ConversationOrchestrator", lambda **_: object())

    application = type("Application", (), {"state": type("State", (), {})()})()

    def fake_create_app(factory, token, **kwargs):
        captured.update(factory=factory, token=token, **kwargs)
        return application

    monkeypatch.setattr(cli, "create_app", fake_create_app)

    result = cli._create_production_app(SESSION_TOKEN)

    assert result is application
    assert captured["db_path"] == tmp_path / "data" / "voxagent.db"
    assert captured["model_path"] == tmp_path / "models" / "embeddings" / "bge-small-zh-v1.5"
    assert captured["knowledge_service"] is knowledge
    assert captured["memory_service"] is memory
    assert captured["persona_service"] is persona_service
    assert captured["data_service"] is data_service
    assert captured["token"] == SESSION_TOKEN
    assert len({id(gate) for gate in gates[:4]}) == 1
    captured["factory"]()
    assert [call["engine"] for call in tts_calls] == ["kokoro"]
    asyncio.run(captured["on_shutdown"]())
    assert database.closed is True
    assert fake_http.closed is True
    assert backup_calls and backup_calls[0][0] is database
