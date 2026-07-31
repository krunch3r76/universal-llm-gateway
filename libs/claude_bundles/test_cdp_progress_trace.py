"""Progress-trace verdicts: advancing vs frozen vs oscillating at abort."""

from __future__ import annotations

import pytest

from claude_bundles.cdp_progress_trace import (
    MAX_TRACE_ENTRIES,
    ProgressTrace,
    fingerprint,
)

NO_PROGRESS_S = 600.0


def _snap(body_len: int, phase: str = "running") -> dict[str, object]:
    return {"completion_phase": phase, "body_len": body_len, "status": "running"}


def _advance(
    trace: ProgressTrace, count: int, *, step: float, start: float = 0.0
) -> float:
    at = start
    for i in range(count):
        at = start + step * i
        trace.record(fingerprint(_snap(i)), at_s=at)
    return at


@pytest.mark.offline
def test_first_record_seeds_baseline_without_counting_a_change() -> None:
    trace = ProgressTrace()
    assert trace.record(fingerprint(_snap(0)), at_s=0.0) is False
    assert trace.changes == 0
    assert trace.verdict(now_s=10.0, no_progress_s=NO_PROGRESS_S) == "never_advanced"


@pytest.mark.offline
def test_advancing_fingerprint_reports_advancing() -> None:
    trace = ProgressTrace()
    last = _advance(trace, 10, step=30.0)
    assert trace.changes == 9
    assert trace.verdict(now_s=last + 5.0, no_progress_s=NO_PROGRESS_S) == "advancing"


@pytest.mark.offline
def test_frozen_tail_before_abort_reports_frozen() -> None:
    trace = ProgressTrace()
    _advance(trace, 5, step=10.0)
    payload = trace.as_dict(now_s=1800.0, no_progress_s=NO_PROGRESS_S)
    assert payload["verdict"] == "frozen"
    assert payload["frozen_for_s"] == pytest.approx(1760.0)
    assert payload["elapsed_s"] == pytest.approx(1800.0)


@pytest.mark.offline
def test_oscillation_between_two_states_is_not_advancing() -> None:
    trace = ProgressTrace()
    a, b = fingerprint(_snap(1)), fingerprint(_snap(2))
    for i, value in enumerate([a, b, a, b, a]):
        trace.record(value, at_s=float(i) * 10.0)
    assert trace.revisits >= 2
    assert trace.verdict(now_s=45.0, no_progress_s=NO_PROGRESS_S) == "oscillating"


@pytest.mark.offline
def test_history_is_bounded_and_records_what_it_dropped() -> None:
    trace = ProgressTrace()
    _advance(trace, MAX_TRACE_ENTRIES + 7, step=1.0)
    payload = trace.as_dict(now_s=100.0, no_progress_s=NO_PROGRESS_S)
    assert len(payload["history"]) == MAX_TRACE_ENTRIES
    assert payload["history_dropped"] == 7
    assert payload["history"][-1]["body_len"] == MAX_TRACE_ENTRIES + 6


@pytest.mark.offline
def test_phase_at_abort_comes_from_the_last_polled_snapshot() -> None:
    trace = ProgressTrace()
    trace.record(fingerprint(_snap(1, phase="running")), at_s=0.0)
    trace.record(fingerprint(_snap(2, phase="turn_idle")), at_s=10.0)
    payload = trace.as_dict(now_s=20.0, no_progress_s=NO_PROGRESS_S)
    assert payload["phase_at_abort"] == "turn_idle"
