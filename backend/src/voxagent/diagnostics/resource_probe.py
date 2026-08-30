from __future__ import annotations

import csv
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from io import StringIO
from statistics import median
from time import monotonic
from typing import Literal

import psutil


@dataclass(frozen=True, slots=True)
class ProbeConfig:
    model_id: str
    mode: Literal["observe", "soak"]
    duration_seconds: float
    sample_interval_ms: int
    stop_available_ram_gib: float
    stop_runner_rss_mib: int


@dataclass(frozen=True, slots=True)
class ResourceSample:
    timestamp_utc: datetime
    runner_rss_bytes: tuple[int, ...]
    vram_mib: int
    system_available_bytes: int

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp_utc": self.timestamp_utc.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "runner_rss_bytes": list(self.runner_rss_bytes),
            "vram_mib": self.vram_mib,
            "system_available_bytes": self.system_available_bytes,
        }


@dataclass(frozen=True, slots=True)
class ProbeReport:
    config: ProbeConfig
    samples: tuple[ResourceSample, ...]
    stop_reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "config": asdict(self.config),
            "summary": summarize_samples(self.samples),
            "stop_reason": self.stop_reason,
            "safety": "Stops only this harness; it never terminates Ollama or other processes.",
            "samples": [sample.to_dict() for sample in self.samples],
        }


def summarize_samples(samples: tuple[ResourceSample, ...]) -> dict[str, object]:
    if not samples:
        raise ValueError("At least one resource sample is required")
    intervals = [
        (current.timestamp_utc - previous.timestamp_utc).total_seconds() * 1000
        for previous, current in zip(samples, samples[1:], strict=False)
    ]
    interval_summary = (
        {
            "minimum": round(min(intervals), 3),
            "median": round(median(intervals), 3),
            "maximum": round(max(intervals), 3),
        }
        if intervals
        else None
    )
    return {
        "sample_count": len(samples),
        "peak_runner_rss_bytes": max(sum(sample.runner_rss_bytes) for sample in samples),
        "peak_vram_mib": max(sample.vram_mib for sample in samples),
        "minimum_available_ram_bytes": min(sample.system_available_bytes for sample in samples),
        "observed_sample_interval_ms": interval_summary,
    }


def build_probe_report(
    config: ProbeConfig,
    samples: tuple[ResourceSample, ...],
    stop_reason: str,
) -> ProbeReport:
    summarize_samples(samples)
    return ProbeReport(config=config, samples=samples, stop_reason=stop_reason)


def memory_pressure_reason(sample: ResourceSample, config: ProbeConfig) -> str | None:
    if sample.system_available_bytes <= int(config.stop_available_ram_gib * 1024**3):
        return "system_available_ram_below_limit"
    if sum(sample.runner_rss_bytes) >= config.stop_runner_rss_mib * 1024**2:
        return "runner_rss_at_or_above_limit"
    return None


def _gpu_vram_mib() -> int:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return 0
    try:
        result = subprocess.run(
            [
                executable,
                "--query-compute-apps=used_gpu_memory",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        rows = csv.reader(StringIO(result.stdout))
        return sum(int(row[0].strip()) for row in rows if row and row[0].strip().isdigit())
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0


def collect_resource_sample() -> ResourceSample:
    rss_values: list[int] = []
    for process in psutil.process_iter(["name", "memory_info"]):
        try:
            name = (process.info["name"] or "").lower()
            if name in {"llama-server", "llama-server.exe"}:
                rss_values.append(int(process.info["memory_info"].rss))
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return ResourceSample(
        timestamp_utc=datetime.now(UTC),
        runner_rss_bytes=tuple(rss_values),
        vram_mib=_gpu_vram_mib(),
        system_available_bytes=int(psutil.virtual_memory().available),
    )


def run_resource_probe(
    config: ProbeConfig,
    *,
    workload: Callable[[], None] | None = None,
    sample_provider: Callable[[], ResourceSample] = collect_resource_sample,
) -> ProbeReport:
    if config.duration_seconds <= 0 or config.sample_interval_ms <= 0:
        raise ValueError("Probe duration and sample interval must be positive")

    samples: list[ResourceSample] = []
    stop_event = threading.Event()
    started = monotonic()
    stop_reason = ["duration_reached"]

    def sample_until_stopped() -> None:
        while not stop_event.is_set():
            sample = sample_provider()
            samples.append(sample)
            pressure = memory_pressure_reason(sample, config)
            if pressure is not None:
                stop_reason[0] = pressure
                stop_event.set()
                return
            remaining = config.duration_seconds - (monotonic() - started)
            if remaining <= 0:
                stop_event.set()
                return
            stop_event.wait(min(config.sample_interval_ms / 1000, remaining))

    sampler = threading.Thread(target=sample_until_stopped, name="voxagent-resource-probe")
    sampler.start()
    if workload is None:
        sampler.join()
    else:
        while not stop_event.is_set():
            try:
                workload()
            except Exception as error:  # noqa: BLE001 - the report must retain workload failure
                stop_reason[0] = f"workload_error:{type(error).__name__}:{error}"
                stop_event.set()
        sampler.join()
    return build_probe_report(config, tuple(samples), stop_reason[0])
