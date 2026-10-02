"""G3 AC2: purpose=review and role=reviewer are job_unknown, not admitted."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline


def _client() -> TestClient:
    root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "services" / "universal-stargate"))
    from systems.proxy.app import app

    return TestClient(app)

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


def test_other_validation_stays_400() -> None:
    client = _client()
    response = client.post("/api/v1/team/dispatch", json={"op": "generate"})
    assert response.status_code == 400
    assert "job_unknown" not in response.text
    assert "dispatch.job.refused" not in response.text
