"""MCP team_dispatch relay surfaces GIW drain-refusal details."""

from __future__ import annotations

from tools.frontier import _surface_worker_refusal_fields


def test_surface_worker_refusal_fields_projects_details() -> None:
    payload = {
        "error": {"code": "GIT_WORKER_DRAINING", "message": "draining"},
        "field": "worker_dispatch",
        "request_id": "req-1",
        "details": {
            "queued": False,
            "reason_code": "giw_draining",
            "retry_after_s": 5,
            "resubmit": "safe",
            "dispatch_id": "disp-1",
        },
    }
    out = _surface_worker_refusal_fields(payload)
    assert out["queued"] is False
    assert out["reason_code"] == "giw_draining"
    assert out["retry_after_s"] == 5
    assert out["resubmit"] == "safe"
    assert out["dispatch_id"] == "disp-1"
    assert out["details"]["queued"] is False
