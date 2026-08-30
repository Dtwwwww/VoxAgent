from voxagent.diagnostics.hardware import (
    DiskSnapshot,
    GpuSnapshot,
    HardwareSnapshot,
    evaluate_preflight,
)


def snapshot(*, c_free: float = 20, d_free: float = 100, ram_free: float = 8, vram: int = 6144):
    return HardwareSnapshot(
        cpu_name="Intel Core i5-9300H",
        cpu_cores=4,
        cpu_threads=8,
        ram_total_gb=15.88,
        ram_free_gb=ram_free,
        gpu=GpuSnapshot("NVIDIA GeForce GTX 1660 Ti", vram, 5000, "572.16", "7.5"),
        disks=(DiskSnapshot("C:\\", 200, c_free), DiskSnapshot("D:\\", 557, d_free)),
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
