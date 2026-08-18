"""Unit tests for sdk_liveness probe classification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_bus_store.sdk_liveness import (
    LivenessVerdict,
    ProbeResult,
    classify_probe,
    evaluate_link_liveness,
    heartbeat_freshness,
    parse_ts,
)


def _fresh_ts() -> str:
    return datetime.now(UTC).isoformat()


def _stale_ts() -> str:
    return (datetime.now(UTC) - timedelta(seconds=301)).isoformat()


def test_heartbeat_null_is_live() -> None:
    assert heartbeat_freshness(None) == "live"


def test_heartbeat_fresh_is_live() -> None:
    assert heartbeat_freshness(_fresh_ts()) == "live"


def test_heartbeat_stale_over_300() -> None:
    assert heartbeat_freshness(_stale_ts()) == "stale"


def test_heartbeat_at_299s_is_live() -> None:
    ts = (datetime.now(UTC) - timedelta(seconds=299)).isoformat()
    assert heartbeat_freshness(ts) == "live"


def test_heartbeat_malformed_is_indeterminate() -> None:
    assert heartbeat_freshness("not-a-timestamp") == "indeterminate"


def test_heartbeat_future_is_indeterminate() -> None:
    future = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    assert heartbeat_freshness(future) == "indeterminate"


def test_classify_running_fresh_skips_orphan() -> None:
    probe = ProbeResult(
        payload={
            "status": "running",
            "execution_id": "exec-1",
            "last_heartbeat_at": _fresh_ts(),
        },
        http_status=200,
        error=None,
    )
    verdict, reason, terminal = classify_probe(probe, link_execution_id="exec-1")
    assert verdict is LivenessVerdict.SKIP_LIVE
    assert reason == "worker_live"
    assert terminal is None


def test_classify_running_null_heartbeat_skips_orphan() -> None:
    probe = ProbeResult(
        payload={"status": "running", "execution_id": "exec-1"},
        http_status=200,
        error=None,
    )
    verdict, _, _ = classify_probe(probe, link_execution_id="exec-1")
    assert verdict is LivenessVerdict.SKIP_LIVE


def test_classify_status_none_allows_orphan() -> None:
    probe = ProbeResult(
        payload={"thread_id": "t1", "status": None},
        http_status=200,
        error=None,
    )
    verdict, reason, _ = classify_probe(probe, link_execution_id="exec-1")
    assert verdict is LivenessVerdict.ALLOW_ORPHAN
    assert reason == "probe_status_null"


def test_classify_404_allows_orphan() -> None:
    probe = ProbeResult(payload=None, http_status=404, error=None)
    verdict, reason, _ = classify_probe(probe, link_execution_id="exec-1")
    assert verdict is LivenessVerdict.ALLOW_ORPHAN
    assert reason == "probe_not_found"


def test_classify_timeout_defers() -> None:
    probe = ProbeResult(
        payload=None, http_status=None, error="probe_unreachable:timed out"
    )
    verdict, reason, _ = classify_probe(probe, link_execution_id="exec-1")
    assert verdict is LivenessVerdict.DEFER
    assert reason.startswith("probe_unreachable")


def test_classify_stale_heartbeat_allows_orphan() -> None:
    probe = ProbeResult(
        payload={
            "status": "running",
            "execution_id": "exec-1",
            "last_heartbeat_at": _stale_ts(),
        },
        http_status=200,
        error=None,
    )
    verdict, reason, _ = classify_probe(probe, link_execution_id="exec-1")
    assert verdict is LivenessVerdict.ALLOW_ORPHAN
    assert reason == "heartbeat_stale"


def test_classify_malformed_heartbeat_defers() -> None:
    probe = ProbeResult(
        payload={
            "status": "running",
            "execution_id": "exec-1",
            "last_heartbeat_at": "bad-ts",
        },
        http_status=200,
        error=None,
    )
    verdict, reason, _ = classify_probe(probe, link_execution_id="exec-1")
    assert verdict is LivenessVerdict.DEFER
    assert reason == "heartbeat_indeterminate"


def test_classify_execution_id_mismatch_allows_orphan() -> None:
    probe = ProbeResult(
        payload={
            "status": "running",
            "execution_id": "exec-other",
            "last_heartbeat_at": _fresh_ts(),
        },
        http_status=200,
        error=None,
    )
    verdict, reason, _ = classify_probe(probe, link_execution_id="exec-1")
    assert verdict is LivenessVerdict.ALLOW_ORPHAN
    assert reason == "execution_id_mismatch"


def test_classify_completed_backfills_without_orphan() -> None:
    probe = ProbeResult(
        payload={"status": "completed", "execution_id": "exec-1"},
        http_status=200,
        error=None,
    )
    verdict, reason, terminal = classify_probe(probe, link_execution_id="exec-1")
    assert verdict is LivenessVerdict.TERMINAL_BACKFILL
    assert reason == "probe_terminal"
    assert terminal == "completed"


def test_classify_failed_backfills_without_orphan() -> None:
    probe = ProbeResult(
        payload={"status": "failed", "execution_id": "exec-1"},
        http_status=200,
        error=None,
    )
    verdict, _, terminal = classify_probe(probe, link_execution_id="exec-1")
    assert verdict is LivenessVerdict.TERMINAL_BACKFILL
    assert terminal == "failed"


def test_classify_parked_waiting_fresh_skips_orphan() -> None:
    probe = ProbeResult(
        payload={
            "status": "parked_waiting",
            "execution_id": "exec-1",
            "last_heartbeat_at": _fresh_ts(),
        },
        http_status=200,
        error=None,
    )
    verdict, reason, terminal = classify_probe(probe, link_execution_id="exec-1")
    assert verdict is LivenessVerdict.SKIP_LIVE
    assert reason == "worker_live"
    assert terminal is None


def test_classify_parked_waiting_stale_heartbeat_still_skips() -> None:
    """Nest park: GIW liveness is the child task, not parent heartbeat age."""
    probe = ProbeResult(
        payload={
            "status": "parked_waiting",
            "execution_id": "exec-1",
            "last_heartbeat_at": _stale_ts(),
        },
        http_status=200,
        error=None,
    )
    verdict, reason, _ = classify_probe(probe, link_execution_id="exec-1")
    assert verdict is LivenessVerdict.SKIP_LIVE
    assert reason == "worker_live"


def test_evaluate_link_liveness_delegates_to_probe_fn() -> None:
    def _fake_probe(_thread_id: str) -> ProbeResult:
        return ProbeResult(
            payload={"status": "running", "execution_id": "exec-x"},
            http_status=200,
            error=None,
        )

    verdict, _, _ = evaluate_link_liveness(
        thread_id="thread-1",
        link_execution_id="exec-x",
        probe_fn=_fake_probe,
    )
    assert verdict is LivenessVerdict.SKIP_LIVE


def test_parse_ts_z_suffix() -> None:
    ts = parse_ts("2026-07-13T03:20:48.425Z")
    assert ts.tzinfo is not None
