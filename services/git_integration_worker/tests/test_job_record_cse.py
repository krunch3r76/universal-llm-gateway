"""AutoJob ledger record_json must persist CSE identity across restart."""

from __future__ import annotations

import json

from services.git_integration_worker.cursor_auto.job_record import (
    job_from_row,
    job_record,
    observer_admission_fields,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob

_URL = "https://claude.ai/cowork/cse_01CodB7tom1281iY8BmZJcZM"


def _job(**overrides: object) -> AutoJob:
    payload = {
        "job_id": "j1",
        "thread_id": "9501",
        "turn_number": 68,
        "subject": "IMPLEMENT",
        "body": "TYPE: DIRECTIVE",
        "from_agent": "web-anthropic",
        "to_agent": "cursor",
        "desired_model": "auto",
        "desired_effort": "high",
        "contract": "implement",
        "cse_chat_url": _URL,
        "cse_registration_id": "reg-a",
    }
    payload.update(overrides)
    return AutoJob(**payload)  # type: ignore[arg-type]


def test_job_record_round_trip_restores_cse() -> None:
    job = _job()
    record = job_record(job)
    assert record["cse_chat_url"] == _URL
    assert record["cse_registration_id"] == "reg-a"

    row = {
        "job_id": job.job_id,
        "thread_id": job.thread_id,
        "turn_number": job.turn_number,
        "request_id": None,
        "status": "queued",
        "record_json": json.dumps(record),
    }
    restored = job_from_row(row)  # type: ignore[arg-type]
    assert restored.cse_chat_url == _URL
    assert restored.cse_registration_id == "reg-a"


def test_job_record_round_trip_preserves_missing_cse() -> None:
    job = _job(cse_chat_url=None, cse_registration_id=None)
    record = job_record(job)
    assert record["cse_chat_url"] is None
    assert record["cse_registration_id"] is None
    row = {
        "job_id": job.job_id,
        "thread_id": job.thread_id,
        "turn_number": job.turn_number,
        "request_id": None,
        "status": "queued",
        "record_json": json.dumps(record),
    }
    restored = job_from_row(row)  # type: ignore[arg-type]
    assert restored.cse_chat_url is None
    assert restored.cse_registration_id is None


def test_job_record_round_trip_restores_advisor_brief() -> None:
    uri = "cortex://notes/system/threads/9530-g1-refire-fable-brief.md"
    job = _job(prompt_uri=uri, advisor_brief="TYPE: CONSULT\nsealed\n")
    record = job_record(job)
    assert record["prompt_uri"] == uri
    assert record["advisor_brief"] == "TYPE: CONSULT\nsealed\n"
    row = {
        "job_id": job.job_id,
        "thread_id": job.thread_id,
        "turn_number": job.turn_number,
        "request_id": None,
        "status": "queued",
        "record_json": json.dumps(record),
    }
    restored = job_from_row(row)  # type: ignore[arg-type]
    assert restored.prompt_uri == uri
    assert restored.advisor_brief == "TYPE: CONSULT\nsealed\n"


def test_observer_admission_fields_round_trip_for_concurrent_claim() -> None:
    job = _job(
        contract="implement",
        lane="B",
        work_key="todo:probe-a",
        execution_mode="isolated_lane_conductor",
    )
    record = job_record(job)
    fields = observer_admission_fields(record)
    assert fields == {
        "work_key": "todo:probe-a",
        "execution_mode": "isolated_lane_conductor",
        "lane": "B",
    }
    row = {
        "job_id": job.job_id,
        "thread_id": job.thread_id,
        "turn_number": job.turn_number,
        "request_id": None,
        "status": "claimed",
        "record_json": json.dumps(record),
    }
    restored = job_from_row(row)  # type: ignore[arg-type]
    assert restored.work_key == "todo:probe-a"
    assert restored.execution_mode == "isolated_lane_conductor"
    assert restored.lane == "B"

    from services.git_integration_worker.cursor_auto.job_lifecycle import (
        observer_view_from_row,
    )

    view = observer_view_from_row(
        {
            "status": "claimed",
            "claimed_at": "2026-09-27T16:54:25.011Z",
            "admitted_at": None,
            "bound_at": None,
            "dispatch_id": None,
            "lifecycle_phase": None,
            "relay_phase": None,
            "job_id": job.job_id,
            "thread_id": job.thread_id,
            "request_id": None,
            "enqueued_at": None,
            "ended_at": None,
            "terminal_reason": None,
            "turn_number": 1,
            "record_json": json.dumps(record),
        }
    )
    assert view["status"] == "claimed"
    assert view["work_key"] == "todo:probe-a"
    assert view["execution_mode"] == "isolated_lane_conductor"
    assert view["lane"] == "B"
