from __future__ import annotations

import pytest
from fastapi import HTTPException, status

from cortex_store.models import SupersedeRequest
from cortex_store.routes.assertions import (
    _create_assertion_impl,
    _supersede_assertion_impl,
    _update_assertion_impl,
)


def _assert_payload_422(exc: HTTPException) -> None:
    assert exc.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert isinstance(exc.detail, dict)
    assert exc.detail["error"] == "assertion_payload_invalid"
    assert exc.detail["diagnostics"]


def test_create_assertion_impl_maps_payload_validation_to_422() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _create_assertion_impl(
            {
                "entity_id": "entity:test",
                "claim": "A test claim.",
                "confidence": "confirmed",
                "evidence": "unit test",
                "artifact_uri": "dropbox/staged.pdf",
            }
        )

    _assert_payload_422(exc_info.value)


def test_supersede_assertion_impl_maps_payload_validation_to_422() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _supersede_assertion_impl(
            {
                "old_assertion_id": 1,
                "entity_id": "entity:test",
                "claim": "Replacement claim.",
                "confidence": "confirmed",
                "evidence": "unit test",
                "evidence_uris": ["files://dropbox/staged.pdf"],
                "session_id": "test-session",
                "agent": "test",
            }
        )

    _assert_payload_422(exc_info.value)


def test_update_assertion_impl_maps_payload_validation_to_422() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _update_assertion_impl(1, {"confidence": "not_a_valid_confidence_value"})

    _assert_payload_422(exc_info.value)


def test_supersede_request_defaults_missing_session_context() -> None:
    req = SupersedeRequest(
        old_assertion_id=1,
        entity_id="entity:test",
        claim="Replacement claim.",
        confidence="believed",
        evidence="unit test",
    )
    assert req.agent == "unknown"
    assert req.session_id.startswith("unknown-")


def test_supersede_request_defaults_agent_from_seeded_by() -> None:
    req = SupersedeRequest(
        old_assertion_id=1,
        entity_id="entity:test",
        claim="Replacement claim.",
        confidence="believed",
        evidence="unit test",
        seeded_by="claude-web",
    )
    assert req.agent == "claude-web"
    assert req.session_id.startswith("claude-web-")


def test_op_supersede_accepts_missing_session_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cortex_store.dispatch_ops.ops_assertions_update import _op_supersede

    captured: dict[str, object] = {}

    def _fake_impl(payload: dict[str, object]) -> dict[str, object]:
        captured.update(payload)
        return {"old": {"id": 1}, "new": {"id": 2}}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_update._supersede_assertion_impl",
        _fake_impl,
    )
    result = _op_supersede(
        old_assertion_id=1,
        entity_id="entity:test",
        claim="Replacement claim.",
        confidence="believed",
        evidence="unit test",
        seeded_by="claude-web",
    )
    assert "error" not in result
    assert captured.get("session_id") is None
    assert captured.get("agent") is None
    assert captured.get("seeded_by") == "claude"
