from __future__ import annotations

import csv
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
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
    model_manifest_sha256: str | None = None
    model_blob_digest: str | None = None
    runner_pid: int | None = None


@dataclass(frozen=True, slots=True)
class RunnerProcess:
    pid: int
    name: str
    command_line: str
    rss_bytes: int


class TargetAttributionError(RuntimeError):
    pass


class GpuMemoryUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GpuMemoryObservation:
    vram_mib: int | None
    attribution: Literal[
        "target_pid",
        "unique_compute_process_total_gpu",
        "unavailable",
    ]
    error: str | None = None
    observed_compute_pids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class ResourceSample:
    timestamp_utc: datetime
    runner_rss_bytes: tuple[int, ...]
    vram_mib: int
    vram_attribution: str
    system_available_bytes: int

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp_utc": self.timestamp_utc.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "runner_rss_bytes": list(self.runner_rss_bytes),
            "vram_mib": self.vram_mib,
            "vram_attribution": self.vram_attribution,
            "system_available_bytes": self.system_available_bytes,
        }


@dataclass(frozen=True, slots=True)
class ProbeReport:
    config: ProbeConfig
    samples: tuple[ResourceSample, ...]
    stop_reason: str
    sampler_error: str | None = None

    def to_dict(self) -> dict[str, object]:
        attribution_modes = {sample.vram_attribution for sample in self.samples}
        if len(attribution_modes) == 1:
            gpu_attribution = next(iter(attribution_modes))
        elif not attribution_modes and self.sampler_error and self.sampler_error.startswith(
            "GpuMemoryUnavailable:"
        ):
            gpu_attribution = "unavailable"
        else:
            gpu_attribution = "mixed_or_unknown"
        return {
            "schema_version": 3,
            "model_id": self.config.model_id,
            "target_identity": {
                "model_manifest_sha256": self.config.model_manifest_sha256,
                "model_blob_digest": self.config.model_blob_digest,
                "runner_pid": self.config.runner_pid,
                "attribution": "strict_target_pid",
            },
            "configured_sample_interval_ms": self.config.sample_interval_ms,
            "gpu_attribution": gpu_attribution,
            "config": asdict(self.config),
            "summary": summarize_samples(self.samples),
            "stop_reason": self.stop_reason,
            "sampler_error": self.sampler_error,
            "safety": "Stops only this harness; it never terminates Ollama or other processes.",
            "samples": [sample.to_dict() for sample in self.samples],
        }


def summarize_samples(samples: tuple[ResourceSample, ...]) -> dict[str, object]:
    if not samples:
        return {
            "sample_count": 0,
            "peak_runner_rss_bytes": 0,
            "peak_vram_mib": 0,
            "minimum_available_ram_bytes": None,
            "observed_sample_interval_ms": None,
        }
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
    sampler_error: str | None = None,
) -> ProbeReport:
    return ProbeReport(
        config=config,
        samples=samples,
        stop_reason=stop_reason,
        sampler_error=sampler_error,
    )


def memory_pressure_reason(sample: ResourceSample, config: ProbeConfig) -> str | None:
    if sample.system_available_bytes <= int(config.stop_available_ram_gib * 1024**3):
        return "system_available_ram_below_limit"
    if sum(sample.runner_rss_bytes) >= config.stop_runner_rss_mib * 1024**2:
        return "runner_rss_at_or_above_limit"
    return None


def parse_gpu_vram_for_pid(output: str, target_pid: int) -> GpuMemoryObservation:
    compute_pids: set[int] = set()
    target_memory: list[int] = []
    target_memory_unavailable = False
    target_memory_invalid = False
    for row in csv.reader(StringIO(output)):
        if len(row) < 2:
            continue
        try:
            pid = int(row[0].strip())
        except ValueError:
            continue
        compute_pids.add(pid)
        if pid == target_pid:
            try:
                target_memory.append(int(row[1].strip()))
            except ValueError:
                if row[1].strip().lower() in {"[n/a]", "n/a"}:
                    target_memory_unavailable = True
                else:
                    target_memory_invalid = True
    observed_pids = tuple(sorted(compute_pids))
    if target_pid not in compute_pids:
        return GpuMemoryObservation(
            vram_mib=None,
            attribution="unavailable",
            error="target PID is absent from nvidia-smi compute-app rows",
            observed_compute_pids=observed_pids,
        )
    if target_memory_invalid:
        return GpuMemoryObservation(
            vram_mib=None,
            attribution="unavailable",
            error="target per-PID VRAM value is invalid",
            observed_compute_pids=observed_pids,
        )
    if target_memory_unavailable or not target_memory:
        return GpuMemoryObservation(
            vram_mib=None,
            attribution="unavailable",
            error="target per-PID VRAM is unavailable",
            observed_compute_pids=observed_pids,
        )
    return GpuMemoryObservation(
        vram_mib=sum(target_memory),
        attribution="target_pid",
        observed_compute_pids=observed_pids,
    )


def query_gpu_memory(target_pid: int) -> GpuMemoryObservation:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return GpuMemoryObservation(None, "unavailable", "nvidia-smi is unavailable")
    try:
        result = subprocess.run(
            [
                executable,
                "--query-compute-apps=pid,used_gpu_memory",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        per_pid = parse_gpu_vram_for_pid(result.stdout, target_pid)
    except (OSError, subprocess.SubprocessError, ValueError):
        return GpuMemoryObservation(None, "unavailable", "per-PID nvidia-smi query failed")
    if per_pid.vram_mib is not None:
        return per_pid
    if per_pid.error != "target per-PID VRAM is unavailable":
        return per_pid
    if per_pid.observed_compute_pids != (target_pid,):
        return per_pid
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        device_values = [
            int(row[0].strip())
            for row in csv.reader(StringIO(result.stdout))
            if row and row[0].strip()
        ]
        if len(device_values) != 1:
            raise ValueError("expected exactly one GPU memory value")
    except (OSError, subprocess.SubprocessError, ValueError):
        return GpuMemoryObservation(
            None,
            "unavailable",
            "whole-device nvidia-smi fallback query failed",
            per_pid.observed_compute_pids,
        )
    return GpuMemoryObservation(
        device_values[0],
        "unique_compute_process_total_gpu",
        observed_compute_pids=per_pid.observed_compute_pids,
    )


def collect_runner_processes() -> tuple[RunnerProcess, ...]:
    runners: list[RunnerProcess] = []
    for process in psutil.process_iter(["name", "memory_info"]):
        try:
            name = (process.info["name"] or "").lower()
            if name in {"llama-server", "llama-server.exe"}:
                runners.append(
                    RunnerProcess(
                        pid=process.pid,
                        name=name,
                        command_line=" ".join(process.cmdline()),
                        rss_bytes=int(process.info["memory_info"].rss),
                    )
                )
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return tuple(runners)


def resolve_target_runner_pid(
    model_blob_digest: str,
    processes: tuple[RunnerProcess, ...],
    requested_pid: int | None = None,
) -> int:
    normalized = model_blob_digest.removeprefix("sha256:")
    marker = f"sha256-{normalized}"
    candidates = [process.pid for process in processes if marker in process.command_line]
    if requested_pid is not None:
        if requested_pid not in candidates:
            raise TargetAttributionError("requested PID does not match the target model blob")
        return requested_pid
    if not candidates:
        raise TargetAttributionError("no llama-server runner matches the target model blob")
    if len(candidates) != 1:
        raise TargetAttributionError("ambiguous llama-server runners match the target model blob")
    return candidates[0]


def collect_resource_sample(target_pid: int) -> ResourceSample:
    process = psutil.Process(target_pid)
    gpu = query_gpu_memory(target_pid)
    if gpu.vram_mib is None:
        raise GpuMemoryUnavailable(gpu.error or "GPU memory is unavailable")
    return ResourceSample(
        timestamp_utc=datetime.now(UTC),
        runner_rss_bytes=(int(process.memory_info().rss),),
        vram_mib=gpu.vram_mib,
        vram_attribution=gpu.attribution,
        system_available_bytes=int(psutil.virtual_memory().available),
    )


def run_resource_probe(
    config: ProbeConfig,
    *,
    workload: Callable[[], None] | None = None,
    sample_provider: Callable[[], ResourceSample] | None = None,
) -> ProbeReport:
    if config.duration_seconds <= 0 or config.sample_interval_ms <= 0:
        raise ValueError("Probe duration and sample interval must be positive")

    if sample_provider is None:
        if config.model_blob_digest is None:
            raise TargetAttributionError("model blob digest is required for strict attribution")
        runner_pid = resolve_target_runner_pid(
            config.model_blob_digest,
            collect_runner_processes(),
            config.runner_pid,
        )
        config = replace(config, runner_pid=runner_pid)

        def attributed_sample_provider() -> ResourceSample:
            return collect_resource_sample(runner_pid)

        sample_provider = attributed_sample_provider

    samples: list[ResourceSample] = []
    stop_event = threading.Event()
    started = monotonic()
    stop_reason = ["duration_reached"]
    sampler_error: list[str | None] = [None]

    def sample_until_stopped() -> None:
        try:
            while not stop_event.is_set():
                sample = sample_provider()
                samples.append(sample)
                pressure = memory_pressure_reason(sample, config)
                if pressure is not None:
                    stop_reason[0] = pressure
                    return
                remaining = config.duration_seconds - (monotonic() - started)
                if remaining <= 0:
                    return
                stop_event.wait(min(config.sample_interval_ms / 1000, remaining))
        except Exception as error:  # noqa: BLE001 - sampler failures belong in the report
            sampler_error[0] = f"{type(error).__name__}: {error}"
            if stop_reason[0] == "duration_reached":
                stop_reason[0] = "sampler_error"
        finally:
            stop_event.set()

    sampler = threading.Thread(
        target=sample_until_stopped,
        name="voxagent-resource-probe",
        daemon=True,
    )
    sampler.start()
    deadline = started + config.duration_seconds
    if workload is None:
        while sampler.is_alive() and not stop_event.is_set():
            remaining = deadline - monotonic()
            if remaining <= 0:
                stop_reason[0] = "sampler_deadline_exceeded"
                stop_event.set()
                break
            sampler.join(min(remaining, 0.05))
        if sampler.is_alive() and stop_reason[0] == "sampler_deadline_exceeded":
            sampler.join(0.1)
    else:
        while sampler.is_alive() and not stop_event.is_set():
            if monotonic() >= deadline:
                stop_reason[0] = "sampler_deadline_exceeded"
                stop_event.set()
                break
            try:
                workload()
            except Exception as error:  # noqa: BLE001 - the report must retain workload failure
                stop_reason[0] = f"workload_error:{type(error).__name__}:{error}"
                stop_event.set()
        sampler.join(0.1)
    return build_probe_report(
        config,
        tuple(samples),
        stop_reason[0],
        sampler_error=sampler_error[0],
    )
