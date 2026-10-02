"""G3 AC2: purpose=review and role=reviewer are job_unknown, not admitted."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline


def _client() -> TestClient:
    """Post through the production validation handler without edge-auth middleware."""
    root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "services" / "universal-stargate"))
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError

    from systems.frontier_consult.route import team_router
    from systems.proxy.app import validation_exception_handler

    surface = FastAPI()
    surface.include_router(team_router)
    surface.add_exception_handler(RequestValidationError, validation_exception_handler)
    return TestClient(surface)


_BASE = {
    "op": "generate",
    "job": "freeform",
    "prompt": "hello",
    "dispatch_thread_id": "dt-1",
}


def _assert_job_unknown(body: dict, field: str) -> None:
    client = _client()
    response = client.post("/api/v1/team/dispatch", json=body)
    payload = response.json()
    assert response.status_code == 422
    assert payload["field"] == field
    assert payload["error"]["code"] == "job_unknown"
    assert payload["error"]["event"] == "dispatch.job.refused"
    assert payload["error"]["reason"] == "job_unknown"
    assert payload["details"]["event"] == "dispatch.job.refused"
    assert payload["details"]["reason"] == "job_unknown"
    assert payload["details"]["registry_ref"] == "job_vocab:unresolved"
    assert payload["details"]["reason"] != "job_retired"
    assert "job_retired" not in response.text


def test_purpose_review_is_job_unknown() -> None:
    _assert_job_unknown({**_BASE, "purpose": "review"}, "purpose")


def test_role_reviewer_is_job_unknown() -> None:
    _assert_job_unknown({**_BASE, "role": "reviewer"}, "role")


def _post(body: dict) -> tuple[int, dict, str]:
    client = _client()
    response = client.post("/api/v1/team/dispatch", json=body)
    return response.status_code, response.json(), response.text


def test_post_job_missing_is_422() -> None:
    """Missing job is intake grammar. Breaks when pydantic 400s before the event."""
    status, payload, text = _post(
        {"op": "generate", "prompt": "hello", "dispatch_thread_id": "dt-1"}
    )
    assert status == 422
    assert payload["field"] == "job"
    assert payload["error"]["code"] == "job_missing"
    assert payload["details"]["event"] == "dispatch.job.refused"
    assert payload["details"]["reason"] == "job_missing"
    assert "job_unknown" not in text


def test_post_retired_token_is_job_unknown() -> None:
    """Retired wire token. Breaks when consult is admitted or the event is dropped."""
    status, payload, _text = _post({**_BASE, "job": "consult"})
    assert status == 422
    assert payload["error"]["code"] == "job_unknown"
    assert payload["details"]["event"] == "dispatch.job.refused"
    assert payload["details"]["registry_ref"] == "job_vocab:unresolved"


def test_post_answer_is_job_unknown_on_generate() -> None:
    """answer is cursor-auto only. Breaks when generate admits it."""
    status, payload, _text = _post({**_BASE, "job": "answer"})
    assert status == 422
    assert payload["error"]["code"] == "job_unknown"
    assert payload["details"]["event"] == "dispatch.job.refused"
    assert payload["details"]["reason"] == "job_unknown"


def test_post_hop_without_job_skips_job_missing() -> None:
    """mission_kind=hop with no job. Breaks when intake emits job_missing."""
    status, payload, text = _post(
        {
            "op": "generate",
            "prompt": "hop",
            "dispatch_thread_id": "dt-1",
            "mission_kind": "hop",
        }
    )
    assert status == 422
    assert payload["error"]["code"] == "role_or_seat_required"
    assert "job_missing" not in text


def test_post_to_thread_job_missing_is_422() -> None:
    """Omitted job on to_thread. Breaks when pydantic 400s before intake."""
    status, payload, text = _post(
        {
            "op": "to_thread",
            "role": "gatherer",
            "dispatch_thread_id": "dt-1",
            "thread": "867",
            "prompt": "hello",
        }
    )
    assert status == 422
    assert payload["field"] == "job"
    assert payload["error"]["code"] == "job_missing"
    assert payload["details"]["reason"] == "job_missing"
    assert "job_unknown" not in text


def test_post_to_thread_consult_is_job_unknown() -> None:
    """Retired consult token on to_thread. Breaks when Literal rejects at parse."""
    status, payload, _text = _post(
        {
            "op": "to_thread",
            "role": "gatherer",
            "dispatch_thread_id": "dt-1",
            "thread": "867",
            "prompt": "hello",
            "job": "consult",
        }
    )
    assert status == 422
    assert payload["field"] == "job"
    assert payload["error"]["code"] == "job_unknown"
    assert payload["details"]["reason"] == "job_unknown"


def test_post_hop_cdp_model_admits_202() -> None:
    """Hop with model=cdp/… and no job. Breaks when route never reaches CDP admit."""
    from unittest.mock import AsyncMock, patch

    admit_payload = {"execution_id": "hop-cdp-exec-1", "status": "running"}

    with patch(
        "systems.frontier_consult.route.dispatch_cdp_generate",
        new_callable=AsyncMock,
        return_value=admit_payload,
    ):
        status, payload, text = _post(
            {
                "op": "generate",
                "model": "cdp/opus-5",
                "prompt": "hop successor",
                "dispatch_thread_id": "dt-hop",
                "mission_kind": "hop",
            }
        )
    assert status == 202, text
    assert payload.get("execution_id") == "hop-cdp-exec-1"
    assert "job_missing" not in text


def test_post_implement_plus_prompt_is_handle_forbidden() -> None:
    """implement plus prompt. Breaks when the model validator returns 400."""
    status, payload, _text = _post(
        {
            "op": "generate",
            "job": "implement",
            "prompt": "edit the repo",
            "dispatch_thread_id": "dt-1",
        }
    )
    assert status == 422
    assert payload["field"] == "prompt"
    assert payload["error"]["code"] == "handle_forbidden"
    assert payload["details"]["event"] == "dispatch.job.refused"
    assert payload["details"]["reason"] == "handle_forbidden"
