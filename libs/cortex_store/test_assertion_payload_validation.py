from __future__ import annotations

import pytest
from fastapi import HTTPException, status

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
