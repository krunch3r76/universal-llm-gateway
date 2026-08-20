"""AutoJob ledger record_json must persist CSE identity across restart."""

from __future__ import annotations

import json

from services.git_integration_worker.cursor_auto.job_record import (
    job_from_row,
    job_record,
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
