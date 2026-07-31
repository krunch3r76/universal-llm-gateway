"""Unit tests for upstream error envelope preservation.

Phase 2 requires that canonical ``ErrorCode`` values surfaced by federated
edges (REQUEST_TIMEOUT, EDGE_UNREACHABLE, etc.) survive the master-side
classification step intact, so the dispatch site can route them to
``record_gateway_timeout`` (DEGRADED) vs ``record_gateway_disconnect``
(UNHEALTHY).

Without envelope preservation, ``map_upstream_status_to_error_code`` would
collapse every 5xx into ``RESOURCE_UNAVAILABLE`` and the breaker would never
see the distinguishing code, defeating the entire DEGRADED/UNHEALTHY split.

Cloud and unstructured upstream paths still need the status-based fallback,
since their bodies do not carry the envelope shape.
"""

from __future__ import annotations

import pytest
from universal_protocol import ErrorCode

from systems.proxy.core.nonstreaming.upstream_error import (
    determine_upstream_error_semantics,
    extract_upstream_envelope_code,
)


def _payload(status_code: int, body_json: dict | None) -> dict:
    """Shape returned by `extract_upstream_error_payload` for a JSON response."""
    return {
        "status_code": status_code,
        "headers": {"content-type": "application/json"},
        "body_json": body_json,
        "body_text": None,
    }


class TestExtractUpstreamEnvelopeCode:
    """Robust extraction across the two envelope shapes the codebase emits."""

    def test_detail_wrapped_envelope(self) -> None:
        """FastAPI HTTPException(detail=error_envelope(...)) → {detail: {code: ...}}."""
        payload = _payload(
            504,
            {
                "detail": {
                    "code": "REQUEST_TIMEOUT",
                    "message": "slow",
                    "source": "edge",
                }
            },
        )

        assert extract_upstream_envelope_code(payload) == "REQUEST_TIMEOUT"

    def test_top_level_envelope(self) -> None:
        """Surfaces that emit the envelope at the top level (no detail wrap)."""
        payload = _payload(
            503,
            {"code": "EDGE_UNREACHABLE", "message": "down", "source": "master"},
        )

        assert extract_upstream_envelope_code(payload) == "EDGE_UNREACHABLE"

    def test_missing_body_json_returns_none(self) -> None:
        payload = {
            "status_code": 503,
            "headers": {},
            "body_json": None,
            "body_text": "x",
        }

        assert extract_upstream_envelope_code(payload) is None

    def test_non_dict_body_json_returns_none(self) -> None:
        """Cloud providers occasionally return JSON arrays / strings."""
        payload = {
            "status_code": 503,
            "headers": {},
            "body_json": ["error", "no capacity"],
            "body_text": None,
        }

        assert extract_upstream_envelope_code(payload) is None

    def test_non_string_code_returns_none(self) -> None:
        """Defensive: malformed envelope where code happens to be numeric."""
        payload = _payload(503, {"detail": {"code": 503}})

        assert extract_upstream_envelope_code(payload) is None

    def test_no_code_field_returns_none(self) -> None:
        """Body has detail but no code (e.g. validation errors from FastAPI itself)."""
        payload = _payload(422, {"detail": [{"loc": ["body"], "msg": "bad"}]})

        assert extract_upstream_envelope_code(payload) is None


class TestDetermineUpstreamErrorSemanticsEnvelopePath:
    """Recognized envelope codes MUST be preserved (Phase 2 contract)."""

    def test_request_timeout_envelope_preserved(self) -> None:
        payload = _payload(504, {"detail": {"code": "REQUEST_TIMEOUT"}})

        error_code, retryable, http_status = determine_upstream_error_semantics(
            504, payload, is_cloud=False
        )

        assert error_code is ErrorCode.REQUEST_TIMEOUT, (
            "504 with REQUEST_TIMEOUT envelope MUST surface REQUEST_TIMEOUT, not"
            " collapse to RESOURCE_UNAVAILABLE — the dispatch site routes this"
            " to record_gateway_timeout (DEGRADED)"
        )
        assert retryable is True
        assert http_status == 504, "504 status preserved per existing rule"

    def test_edge_unreachable_envelope_preserved(self) -> None:
        payload = _payload(503, {"detail": {"code": "EDGE_UNREACHABLE"}})

        error_code, retryable, http_status = determine_upstream_error_semantics(
            503, payload, is_cloud=False
        )

        assert error_code is ErrorCode.EDGE_UNREACHABLE, (
            "503 with EDGE_UNREACHABLE envelope MUST surface EDGE_UNREACHABLE —"
            " the dispatch site routes this to record_gateway_disconnect"
            " (UNHEALTHY)"
        )
        assert retryable is True
        assert http_status == 503

    def test_gateway_disconnected_envelope_preserved(self) -> None:
        payload = _payload(503, {"detail": {"code": "GATEWAY_DISCONNECTED"}})

        error_code, _, _ = determine_upstream_error_semantics(
            503, payload, is_cloud=False
        )

        assert error_code is ErrorCode.GATEWAY_DISCONNECTED

    def test_inference_timeout_envelope_preserved(self) -> None:
        payload = _payload(504, {"detail": {"code": "INFERENCE_TIMEOUT"}})

        error_code, _, _ = determine_upstream_error_semantics(
            504, payload, is_cloud=False
        )

        assert error_code is ErrorCode.INFERENCE_TIMEOUT


class TestDetermineUpstreamErrorSemanticsFallbackPath:
    """When no envelope is present (or unrecognized), fall back to status mapping."""

    def test_503_without_envelope_falls_back_to_resource_unavailable(self) -> None:
        """Pre-Phase-2 default — preserved for cloud capacity transients."""
        payload = _payload(503, None)

        error_code, retryable, http_status = determine_upstream_error_semantics(
            503, payload, is_cloud=False
        )

        assert error_code is ErrorCode.RESOURCE_UNAVAILABLE
        assert retryable is True
        assert http_status == 503

    def test_unknown_envelope_code_falls_back(self) -> None:
        """Defensive: upstream emits a code we don't have in the enum yet."""
        payload = _payload(503, {"detail": {"code": "FUTURE_CODE_NOT_IN_ENUM"}})

        error_code, _, _ = determine_upstream_error_semantics(
            503, payload, is_cloud=False
        )

        assert error_code is ErrorCode.RESOURCE_UNAVAILABLE, (
            "unrecognized envelope code MUST not crash; status-based mapping takes over"
        )

    def test_500_without_envelope_falls_back_and_returns_502(self) -> None:
        """Generic 500 → upstream RESOURCE_UNAVAILABLE, but proxy surfaces 502."""
        payload = _payload(500, {"error": "internal"})

        error_code, retryable, http_status = determine_upstream_error_semantics(
            500, payload, is_cloud=False
        )

        assert error_code is ErrorCode.RESOURCE_UNAVAILABLE
        assert retryable is True
        assert http_status == 502, (
            "non-503/504 5xx must surface as 502 Bad Gateway per existing rule"
        )

    def test_400_with_model_not_loaded_marker_falls_back_correctly(self) -> None:
        """Detail-shape with 'error.code' (not envelope) hits the existing
        model-not-loaded fast path, not the envelope path."""
        payload = _payload(
            400,
            {"error": {"code": "model_not_loaded", "message": "evicted"}},
        )

        error_code, retryable, http_status = determine_upstream_error_semantics(
            400, payload, is_cloud=False
        )

        assert error_code is ErrorCode.RESOURCE_UNAVAILABLE
        assert retryable is True
        assert http_status == 502, "400 from federated edge surfaces as 502"


class TestDetermineUpstreamErrorSemanticsCloudPath:
    """Cloud provider responses retain their HTTP status for client visibility."""

    def test_cloud_4xx_status_preserved(self) -> None:
        """OpenAI/Anthropic 401/429/etc must reach the caller as-is."""
        payload = _payload(429, {"error": {"message": "rate limited"}})

        error_code, retryable, http_status = determine_upstream_error_semantics(
            429, payload, is_cloud=True
        )

        assert http_status == 429
        assert error_code is ErrorCode.RESOURCE_UNAVAILABLE
        assert retryable is True

    def test_cloud_4xx_invalid_request_preserved(self) -> None:
        payload = _payload(401, {"error": {"message": "bad key"}})

        error_code, retryable, http_status = determine_upstream_error_semantics(
            401, payload, is_cloud=True
        )

        assert http_status == 401
        assert error_code is ErrorCode.INVALID_REQUEST
        assert retryable is False


@pytest.mark.parametrize(
    ("upstream_status", "expected_response_status"),
    [
        (500, 502),
        (502, 502),
        (503, 503),
        (504, 504),
        (599, 502),
    ],
)
def test_response_http_status_mapping(
    upstream_status: int, expected_response_status: int
) -> None:
    """503/504 are preserved (load/timeout signals); other 5xx → 502."""
    payload = _payload(upstream_status, None)

    _, _, http_status = determine_upstream_error_semantics(
        upstream_status, payload, is_cloud=False
    )

    assert http_status == expected_response_status
