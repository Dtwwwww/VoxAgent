from datetime import UTC, datetime, timedelta

from voxagent.diagnostics.resource_probe import (
    ProbeConfig,
    ResourceSample,
    build_probe_report,
    memory_pressure_reason,
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
    assert payload["schema_version"] == 1
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
