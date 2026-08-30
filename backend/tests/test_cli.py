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
    )
    monkeypatch.setattr(cli, "collect_hardware", lambda _: snapshot)

    result = CliRunner().invoke(cli.app, ["preflight", "--json", "--data-root", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 2
    assert payload["hardware"]["gpu"] is None
    assert {issue["code"] for issue in payload["issues"]} == {"vram_unsupported"}
