from pathlib import Path
from types import SimpleNamespace

import pytest

from voxagent.config import AppPaths
from voxagent.diagnostics import hardware
from voxagent.diagnostics.hardware import (
    DiskSnapshot,
    GpuSnapshot,
    HardwareSnapshot,
    evaluate_preflight,
)


def snapshot(
    *,
    c_free: float = 20,
    d_free: float = 100,
    ram_free: float = 8,
    vram: int = 6144,
    data_drive: str = "D:\\",
    data_drive_error: str | None = None,
):
    return HardwareSnapshot(
        cpu_name="Intel Core i5-9300H",
        cpu_cores=4,
        cpu_threads=8,
        ram_total_gb=15.88,
        ram_free_gb=ram_free,
        gpu=GpuSnapshot("NVIDIA GeForce GTX 1660 Ti", vram, 5000, "572.16", "7.5"),
        disks=(DiskSnapshot("C:\\", 200, c_free), DiskSnapshot(data_drive, 557, d_free)),
        selected_data_drive=data_drive,
        data_drive_probe_error=data_drive_error,
    )


def test_target_machine_passes_when_headroom_is_available():
    assert evaluate_preflight(snapshot()) == ()


def test_preflight_reports_all_blocking_resource_failures():
    issues = evaluate_preflight(snapshot(c_free=3.7, d_free=10, ram_free=2.5, vram=4096))
    assert {issue.code for issue in issues} == {
        "c_drive_low",
        "data_drive_low",
        "ram_low",
        "vram_unsupported",
    }
    assert all(issue.blocking for issue in issues)


def test_preflight_uses_selected_e_data_drive_when_it_has_required_headroom():
    issues = evaluate_preflight(snapshot(data_drive="E:\\", d_free=20))

    assert "data_drive_low" not in {issue.code for issue in issues}


def test_preflight_reports_only_selected_e_data_drive_when_it_is_low():
    issues = evaluate_preflight(snapshot(data_drive="E:\\", d_free=19.99))

    assert {issue.code for issue in issues} == {"data_drive_low"}


def test_preflight_does_not_infer_data_drive_from_disk_order():
    machine = snapshot(data_drive="E:\\", d_free=50)
    machine = HardwareSnapshot(
        cpu_name=machine.cpu_name,
        cpu_cores=machine.cpu_cores,
        cpu_threads=machine.cpu_threads,
        ram_total_gb=machine.ram_total_gb,
        ram_free_gb=machine.ram_free_gb,
        gpu=machine.gpu,
        disks=(
            DiskSnapshot("C:\\", 200, 20),
            DiskSnapshot("D:\\", 557, 1),
            DiskSnapshot("E:\\", 557, 50),
        ),
        selected_data_drive="E:\\",
        data_drive_probe_error=None,
    )

    assert evaluate_preflight(machine) == ()


def test_preflight_reports_selected_data_drive_as_unavailable():
    machine = snapshot(data_drive="E:\\", data_drive_error="WinError 3: path not found")
    machine = HardwareSnapshot(
        cpu_name=machine.cpu_name,
        cpu_cores=machine.cpu_cores,
        cpu_threads=machine.cpu_threads,
        ram_total_gb=machine.ram_total_gb,
        ram_free_gb=machine.ram_free_gb,
        gpu=machine.gpu,
        disks=(machine.disks[0],),
        selected_data_drive="E:\\",
        data_drive_probe_error=machine.data_drive_probe_error,
    )

    issues = evaluate_preflight(machine)

    assert [issue.code for issue in issues] == ["data_drive_unavailable"]
    assert issues[0].blocking is True


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "NVIDIA GeForce GTX 1660 Ti, 6144",
        "NVIDIA GeForce GTX 1660 Ti, unavailable, 5000, 572.16, 7.5",
    ],
)
def test_gpu_snapshot_returns_none_for_unusable_nvidia_smi_output(monkeypatch, stdout: str):
    monkeypatch.setattr(hardware.shutil, "which", lambda _: "nvidia-smi")
    monkeypatch.setattr(
        hardware.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=stdout),
    )

    assert hardware._gpu_snapshot() is None


def test_collect_hardware_returns_no_gpu_when_nvidia_smi_fails(monkeypatch):
    def failing_run(*args, **kwargs):
        raise hardware.subprocess.CalledProcessError(1, "nvidia-smi")

    monkeypatch.setattr(hardware.shutil, "which", lambda _: "nvidia-smi")
    monkeypatch.setattr(hardware.subprocess, "run", failing_run)
    monkeypatch.setattr(hardware, "_cpu_name", lambda: "CPU")
    monkeypatch.setattr(
        hardware.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(total=16 * 1024**3, available=8 * 1024**3),
    )
    monkeypatch.setattr(hardware.psutil, "cpu_count", lambda logical: 8 if logical else 4)
    monkeypatch.setattr(hardware, "_disk", lambda drive: DiskSnapshot(drive, 100, 50))

    snapshot = hardware.collect_hardware(AppPaths.from_root(Path(r"D:\VoxAgentData")))

    assert snapshot.gpu is None


def test_collect_hardware_captures_selected_drive_probe_failure(monkeypatch):
    monkeypatch.setattr(hardware, "_cpu_name", lambda: "CPU")
    monkeypatch.setattr(hardware, "_gpu_snapshot", lambda: None)
    monkeypatch.setattr(
        hardware.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(total=16 * 1024**3, available=8 * 1024**3),
    )
    monkeypatch.setattr(hardware.psutil, "cpu_count", lambda logical: 8 if logical else 4)

    def probe_disk(drive: str) -> DiskSnapshot:
        if drive == "E:\\":
            raise OSError("selected drive is not mounted")
        return DiskSnapshot(drive, 200, 20)

    monkeypatch.setattr(hardware, "_disk", probe_disk)

    machine = hardware.collect_hardware(AppPaths.from_root(Path(r"E:\VoxAgentData")))

    assert machine.disks == (DiskSnapshot("C:\\", 200, 20),)
    assert machine.selected_data_drive == "E:\\"
    assert machine.data_drive_probe_error == "selected drive is not mounted"
