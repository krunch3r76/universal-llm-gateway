"""A dropped cdp.generate.* event leaves a trace instead of vanishing."""

from __future__ import annotations

import pytest

from systems.frontier_consult import cdp_events


@pytest.fixture(autouse=True)
def _clear_seen():
    cdp_events._SWALLOWED_SEEN.clear()
    cdp_events.reset_horizon_unverifiable_emits_for_tests()
    yield
    cdp_events._SWALLOWED_SEEN.clear()
    cdp_events.reset_horizon_unverifiable_emits_for_tests()


def test_publish_failure_is_logged_once_per_cause(monkeypatch, caplog) -> None:
    def _boom(_event):
        raise RuntimeError("Proxy not initialized")

    monkeypatch.setattr(cdp_events, "publish_cdp_event", _boom)
    with caplog.at_level("WARNING"):
        for _ in range(3):
            cdp_events.publish_cdp_kwargs(
                cdp_events.CdpGenerateAdmitted,
                request_id="r",
                execution_id="e",
                model="cdp/opus-5",
                thread_id="1",
            )
    dropped = [r for r in caplog.records if "cdp event dropped" in r.getMessage()]
    assert len(dropped) == 1
    assert "Proxy not initialized" in dropped[0].getMessage()


def test_publish_never_raises_into_the_lane(monkeypatch) -> None:
    def _boom(_event):
        raise RuntimeError("bus gone")

    monkeypatch.setattr(cdp_events, "publish_cdp_event", _boom)
    cdp_events.publish_cdp_kwargs(
        cdp_events.CdpGenerateStalled,
        request_id="r",
        execution_id="e",
        satellite_execution_id=None,
        stall_stage="wall_clock_exceeded",
        error="boom",
        progress_trace={"verdict": "frozen"},
    )


def test_stalled_payload_carries_progress_trace() -> None:
    event = cdp_events.CdpGenerateStalled(
        request_id="r",
        execution_id="e",
        satellite_execution_id="s",
        stall_stage="wall_clock_exceeded",
        error="CDP generate exceeded max_wall_s=1800",
        progress_trace={"verdict": "frozen", "frozen_for_s": 1760.0},
    )
    assert event.payload["progress_trace"]["verdict"] == "frozen"


def test_stalled_payload_carries_since_last_progress_s() -> None:
    event = cdp_events.CdpGenerateStalled(
        request_id="r",
        execution_id="e",
        satellite_execution_id="s",
        stall_stage="wall_clock_exceeded",
        error="CDP generate no progress for max_wall_s=1800",
        progress_trace={"verdict": "frozen"},
        since_last_progress_s=42.5,
    )
    assert event.payload["since_last_progress_s"] == 42.5
    assert "active_wall_s" not in event.payload
    assert "wall_paused_s" not in event.payload


def test_horizon_unverifiable_retries_until_publish_succeeds(monkeypatch) -> None:
    """Swallowed publish must not consume the once-per-id slot (AC2)."""
    attempts: list[str] = []

    def _fail_twice_then_ok(event) -> bool:
        attempts.append(event.signal)
        if len(attempts) <= 2:
            raise RuntimeError("bus down")
        return True

    monkeypatch.setattr(cdp_events, "publish_cdp_event", _fail_twice_then_ok)
    kwargs = dict(
        request_id="r",
        execution_id="exec-retry",
        satellite_execution_id="s",
        thread_id="9501",
        stall_stage="horizon_unverifiable_retained",
        error="horizon crossed; liveness unverifiable: project-ask HTTP 404",
    )
    assert cdp_events.publish_horizon_unverifiable_once(**kwargs) is False
    assert "exec-retry" not in cdp_events._HORIZON_UNVERIFIABLE_EMITTED
    assert cdp_events.publish_horizon_unverifiable_once(**kwargs) is False
    assert "exec-retry" not in cdp_events._HORIZON_UNVERIFIABLE_EMITTED
    assert cdp_events.publish_horizon_unverifiable_once(**kwargs) is True
    assert "exec-retry" in cdp_events._HORIZON_UNVERIFIABLE_EMITTED
    assert cdp_events.publish_horizon_unverifiable_once(**kwargs) is False
    assert attempts == ["cdp.generate.horizon.unverifiable"] * 3


def test_horizon_unverifiable_payload_carries_thread_and_error() -> None:
    event = cdp_events.CdpGenerateHorizonUnverifiable(
        request_id="r",
        execution_id="e",
        satellite_execution_id="s",
        thread_id="9501",
        stall_stage="horizon_unverifiable_retained",
        error="horizon crossed; liveness unverifiable: project-ask HTTP 404",
    )
    assert event.signal == "cdp.generate.horizon.unverifiable"
    assert event.payload["thread_id"] == "9501"
    assert event.payload["execution_id"] == "e"
    assert event.payload["satellite_execution_id"] == "s"
    assert event.payload["stall_stage"] == "horizon_unverifiable_retained"
    assert "project-ask HTTP 404" in event.payload["error"]


def test_stalled_payload_carries_deliverable_present() -> None:
    event = cdp_events.CdpGenerateStalled(
        request_id="r",
        execution_id="e",
        satellite_execution_id="s",
        stall_stage="completed_without_proof",
        error="chat harvest lacks attested_model",
        archive_uri="cortex://notes/system/threads/cdp-ask-archive-new.md",
        deliverable_present=True,
    )
    assert event.payload["archive_uri"].endswith("cdp-ask-archive-new.md")
    assert event.payload["deliverable_present"] is True
