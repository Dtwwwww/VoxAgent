import json

import pytest
from typer.testing import CliRunner

from voxagent import cli
from voxagent.diagnostics.hardware import DiskSnapshot, HardwareSnapshot


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
