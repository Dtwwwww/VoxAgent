from __future__ import annotations

import pytest

from voxagent.mcp.demo import run_demo


@pytest.mark.asyncio
async def test_stdio_demo_proves_read_write_and_replay_boundaries(tmp_path) -> None:
    report = await run_demo(tmp_path)

    assert report.tools == (
        "knowledge.search",
        "reminders.complete",
        "reminders.create",
        "reminders.list",
    )
    assert report.read_status == "succeeded"
    assert report.unapproved_error == "mcp_capability_rejected"
    assert report.approved_status == "succeeded"
    assert report.replay_error == "mcp_capability_rejected"
    assert report.reminder_count == 1
    assert "request.succeeded" in report.audit_events
