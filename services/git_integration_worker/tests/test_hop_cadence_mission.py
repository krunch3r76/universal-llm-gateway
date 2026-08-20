"""Mission-statement capture from an enrolling hop-cadence job."""

from __future__ import annotations

import pytest

from services.git_integration_worker.cursor_auto.hop_cadence_mission import (
    mission_candidate_from_job,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob

pytestmark = pytest.mark.offline


def _job(*, subject: str = "", body: str = "") -> AutoJob:
    return AutoJob(
        job_id="job-1",
        thread_id="9501",
        turn_number=1,
        subject=subject,
        body=body,
        from_agent="web-anthropic",
        to_agent="cursor-auto",
        desired_model="auto",
        desired_effort="medium",
        contract="light-bounded",
    )


def test_prefers_explicit_vision_line_over_subject() -> None:
    job = _job(
        subject="status:admitted — thread 9501",
        body="TYPE: DIRECTIVE\nvision: Recover the operator-proxy continuity arc.\n",
    )
    assert (
        mission_candidate_from_job(job) == "Recover the operator-proxy continuity arc."
    )


def test_falls_back_to_non_generic_subject() -> None:
    job = _job(subject="Investigate MCP container registry divergence root cause")
    assert (
        mission_candidate_from_job(job)
        == "Investigate MCP container registry divergence root cause"
    )


@pytest.mark.parametrize(
    "subject",
    [
        "status:admitted — thread 9501",
        "continuity harvest residual — abc123",
        "cursor-auto hop cadence — continuity hop thread=9501",
    ],
)
def test_generic_subjects_are_not_a_mission_candidate(subject: str) -> None:
    job = _job(subject=subject)
    assert mission_candidate_from_job(job) is None


def test_no_vision_no_subject_returns_none() -> None:
    job = _job()
    assert mission_candidate_from_job(job) is None


def test_long_candidate_is_clipped() -> None:
    job = _job(subject="x" * 200)
    candidate = mission_candidate_from_job(job)
    assert candidate is not None
    assert len(candidate) == 160
    assert candidate.endswith("…")
