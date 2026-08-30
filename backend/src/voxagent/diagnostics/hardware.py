from __future__ import annotations

import csv
import shutil
import subprocess
import winreg
from dataclasses import asdict, dataclass
from io import StringIO

import psutil

from voxagent.config import AppPaths


@dataclass(frozen=True, slots=True)
class GpuSnapshot:
    name: str
    memory_total_mb: int
    memory_free_mb: int
    driver_version: str
    compute_capability: str


@dataclass(frozen=True, slots=True)
class DiskSnapshot:
    drive: str
    total_gb: float
    free_gb: float


@dataclass(frozen=True, slots=True)
class HardwareSnapshot:
    cpu_name: str
    cpu_cores: int
    cpu_threads: int
    ram_total_gb: float
    ram_free_gb: float
    gpu: GpuSnapshot | None
    disks: tuple[DiskSnapshot, ...]
    selected_data_drive: str
    data_drive_probe_error: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PreflightIssue:
    code: str
    message: str
    blocking: bool


def _gb(value: int) -> float:
    return round(value / (1024**3), 2)


def _gpu_snapshot() -> GpuSnapshot | None:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return None
    command = [
        executable,
        "--query-gpu=name,memory.total,memory.free,driver_version,compute_cap",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)
        row = next(csv.reader(StringIO(result.stdout.strip())))
        return GpuSnapshot(row[0].strip(), int(row[1]), int(row[2]), row[3].strip(), row[4].strip())
    except (csv.Error, IndexError, OSError, StopIteration, subprocess.SubprocessError, ValueError):
        return None


def _disk(drive: str) -> DiskSnapshot:
    usage = shutil.disk_usage(drive)
    return DiskSnapshot(drive, _gb(usage.total), _gb(usage.free))


def _cpu_name() -> str:
    key_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
        value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
    return str(value).strip()


def collect_hardware(paths: AppPaths) -> HardwareSnapshot:
    memory = psutil.virtual_memory()
    selected_data_drive = f"{paths.root.drive.upper()}\\"
    c_disk = _disk("C:\\")
    disks = [c_disk]
    data_drive_probe_error: str | None = None
    if selected_data_drive != "C:\\":
        try:
            disks.append(_disk(selected_data_drive))
        except OSError as error:
            data_drive_probe_error = str(error)
    return HardwareSnapshot(
        cpu_name=_cpu_name(),
        cpu_cores=psutil.cpu_count(logical=False) or 0,
        cpu_threads=psutil.cpu_count(logical=True) or 0,
        ram_total_gb=_gb(memory.total),
        ram_free_gb=_gb(memory.available),
        gpu=_gpu_snapshot(),
        disks=tuple(disks),
        selected_data_drive=selected_data_drive,
        data_drive_probe_error=data_drive_probe_error,
    )


def evaluate_preflight(snapshot: HardwareSnapshot) -> tuple[PreflightIssue, ...]:
    disks = {disk.drive.upper(): disk for disk in snapshot.disks}
    issues: list[PreflightIssue] = []
    if disks.get("C:\\") is None or disks["C:\\"].free_gb < 15:
        issues.append(PreflightIssue("c_drive_low", "C: requires at least 15GB free", True))
    selected_data_drive = snapshot.selected_data_drive.upper()
    data_disk = disks.get(selected_data_drive)
    if snapshot.data_drive_probe_error is not None or data_disk is None:
        detail = snapshot.data_drive_probe_error or "drive was not present in the snapshot"
        issues.append(
            PreflightIssue(
                "data_drive_unavailable",
                f"Selected data drive {selected_data_drive} is unavailable: {detail}",
                True,
            )
        )
    elif data_disk.free_gb < 20:
        issues.append(PreflightIssue("data_drive_low", "Data drive requires 20GB free", True))
    if snapshot.ram_free_gb < 6:
        issues.append(
            PreflightIssue("ram_low", "Close apps until at least 6GB RAM is available", True)
        )
    if snapshot.gpu is None or snapshot.gpu.memory_total_mb < 6000:
        issues.append(
            PreflightIssue("vram_unsupported", "A 6GB NVIDIA GPU is required for 4B mode", True)
        )
    return tuple(issues)
