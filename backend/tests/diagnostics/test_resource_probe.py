from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic

import pytest

from voxagent.diagnostics.resource_probe import (
    ProbeConfig,
    ResourceSample,
    RunnerProcess,
    TargetAttributionError,
    build_probe_report,
    memory_pressure_reason,
    parse_gpu_vram_for_pid,
    resolve_target_runner_pid,
    run_resource_probe,
)


def _sample(
    offset_ms: int,
    *,
    rss_bytes: tuple[int, ...] = (1024,),
    vram_mib: int = 100,
    available_bytes: int = 8 * 1024**3,
) -> ResourceSample:
    return ResourceSample(
        timestamp_utc=datetime(2026, 8, 30, tzinfo=UTC) + timedelta(milliseconds=offset_ms),
        runner_rss_bytes=rss_bytes,
        vram_mib=vram_mib,
        system_available_bytes=available_bytes,
    )


def test_probe_report_schema_and_summary_are_recomputable():
    config = ProbeConfig(
        model_id="test-model",
        mode="observe",
        duration_seconds=1,
        sample_interval_ms=100,
        stop_available_ram_gib=0.5,
        stop_runner_rss_mib=7000,
    )
    report = build_probe_report(
        config,
        (_sample(0), _sample(100, rss_bytes=(1024, 2048), vram_mib=300)),
        stop_reason="duration_reached",
    )

    payload = report.to_dict()
    assert payload["schema_version"] == 2
    assert payload["config"]["sample_interval_ms"] == 100
    assert payload["summary"] == {
        "sample_count": 2,
        "peak_runner_rss_bytes": 3072,
        "peak_vram_mib": 300,
        "minimum_available_ram_bytes": 8 * 1024**3,
        "observed_sample_interval_ms": {"minimum": 100.0, "median": 100.0, "maximum": 100.0},
    }
    assert payload["stop_reason"] == "duration_reached"


def test_memory_pressure_stops_observation_without_process_termination():
    config = ProbeConfig(
        model_id="test-model",
        mode="soak",
        duration_seconds=1800,
        sample_interval_ms=100,
        stop_available_ram_gib=0.5,
        stop_runner_rss_mib=7000,
    )

    assert memory_pressure_reason(_sample(0), config) is None
    assert (
        memory_pressure_reason(
            _sample(0, available_bytes=int(0.49 * 1024**3)),
            config,
        )
        == "system_available_ram_below_limit"
    )
    assert (
        memory_pressure_reason(
            _sample(0, rss_bytes=(7000 * 1024**2,)),
            config,
        )
        == "runner_rss_at_or_above_limit"
    )


def test_target_blob_resolves_to_exactly_one_runner_pid():
    processes = (
        RunnerProcess(10, "llama-server.exe", "--model sha256-target", 100),
        RunnerProcess(20, "llama-server.exe", "--model sha256-other", 200),
    )

    assert resolve_target_runner_pid("target", processes) == 10

    with pytest.raises(TargetAttributionError, match="ambiguous"):
        resolve_target_runner_pid(
            "target",
            processes + (RunnerProcess(30, "llama-server.exe", "--model sha256-target", 300),),
        )


def test_gpu_vram_is_counted_only_for_target_pid():
    output = "10, 2048\n20, 4096\n10, 512\n"

    assert parse_gpu_vram_for_pid(output, 10) == 2560


def test_sampler_error_is_recorded_and_returns_without_hanging():
    config = ProbeConfig(
        model_id="test-model",
        mode="observe",
        duration_seconds=30,
        sample_interval_ms=100,
        stop_available_ram_gib=0.5,
        stop_runner_rss_mib=7000,
        model_manifest_sha256="a" * 64,
        model_blob_digest="b" * 64,
        runner_pid=10,
    )

    def broken_sampler():
        raise RuntimeError("sampler broke")

    report = run_resource_probe(config, sample_provider=broken_sampler)

    payload = report.to_dict()
    assert payload["stop_reason"] == "sampler_error"
    assert payload["sampler_error"] == "RuntimeError: sampler broke"
    assert payload["samples"] == []


def test_blocked_sampler_is_bounded_by_main_deadline():
    config = ProbeConfig(
        model_id="test-model",
        mode="observe",
        duration_seconds=0.01,
        sample_interval_ms=10,
        stop_available_ram_gib=0.5,
        stop_runner_rss_mib=7000,
        model_manifest_sha256="a" * 64,
        model_blob_digest="b" * 64,
        runner_pid=10,
    )
    release = Event()

    def blocked_sampler():
        release.wait(1.5)
        raise RuntimeError("released")

    started = monotonic()
    report = run_resource_probe(config, sample_provider=blocked_sampler)
    elapsed = monotonic() - started
    release.set()

    assert elapsed < 1
    assert report.stop_reason == "sampler_deadline_exceeded"
