import json

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
