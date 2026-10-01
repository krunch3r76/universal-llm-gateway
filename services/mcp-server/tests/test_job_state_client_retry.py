"""fetch_job_state after the Auto worker route was removed.

The HTTP retry ladder is gone with ``httpx``. A keyed fetch is one shot and
reports ``auto_arm_removed``.
"""

from __future__ import annotations

from unittest.mock import patch

from tools.agent_bus.job_state_client import fetch_job_state


def test_fetch_job_state_retries_unreachable_then_hits() -> None:
    with patch("tools.agent_bus.job_state_client.time.sleep") as sleep:
        result = fetch_job_state(job_id="j1", backoff_s=(0.0, 0.0))

    assert result["found"] is False
    assert result["reason"] == "auto_arm_removed"
    assert result["attempts"] == 1
    assert sleep.call_count == 0


def test_fetch_job_state_not_found_does_not_retry() -> None:
    result = fetch_job_state(job_id="missing")

    assert result["found"] is False
    assert result["reason"] == "auto_arm_removed"
    assert result["attempts"] == 1


def test_fetch_job_state_legacy_timeout_s_is_single_shot() -> None:
    result = fetch_job_state(job_id="j1", timeout_s=3.0)

    assert result["reason"] == "auto_arm_removed"
    assert result["attempts"] == 1


def test_fetch_job_state_missing_key() -> None:
    result = fetch_job_state()
    assert result["reason"] == "missing_key"
    assert result["found"] is False
