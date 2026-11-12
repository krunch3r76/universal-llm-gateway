"""Sparse document read payload envelope (slice 1)."""

from __future__ import annotations

from typing import Any

# 65536 code points × 4 UTF-8 bytes max (BMP scalar) — defensive wire ceiling.
SPARSE_MAX_CONTENT_BYTES = 262_144
SPARSE_POLICY_SLICE1 = "slice1_document_and_entity_preview"


def _sum_utf8_content_bytes(node: Any) -> int:
    total = 0
    if isinstance(node, dict):
        for key, val in node.items():
            if key == "content" and isinstance(val, str):
                total += len(val.encode("utf-8"))
            else:
                total += _sum_utf8_content_bytes(val)
    elif isinstance(node, list):
        for item in node:
            total += _sum_utf8_content_bytes(item)
    return total


def finalize_sparse_document_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Attach sparse policy marker and enforce the UTF-8 byte ceiling (slice 1)."""
    if payload.get("code") in {"param_not_applicable", "invalid_selector_combo"}:
        return payload
    selection = payload.get("selection")
    if selection == "multi":
        byte_sum = _sum_utf8_content_bytes(payload)
        if byte_sum <= SPARSE_MAX_CONTENT_BYTES:
            payload["sparse_policy"] = SPARSE_POLICY_SLICE1
        return payload
    if selection not in {"manifest", "section", "sections", "export", "window"}:
        return payload
    byte_sum = _sum_utf8_content_bytes(payload)
    if byte_sum > SPARSE_MAX_CONTENT_BYTES:
        return {
            "code": "sparse_payload_over_byte_ceiling",
            "message": (
                f"Sparse document response exceeded UTF-8 content ceiling "
                f"({byte_sum} bytes > {SPARSE_MAX_CONTENT_BYTES})"
            ),
            "source": "fs",
            "retryable": False,
            "data": {"measured_utf8_bytes": byte_sum},
        }
    payload["sparse_policy"] = SPARSE_POLICY_SLICE1
    return payload


def _stale_read_root(*, read_sha256: str, expected_read_sha256: str) -> dict[str, Any]:
    return finalize_sparse_document_payload(
        {
            "ok": False,
            "outcome": "stale_handle",
            "read_sha256": read_sha256,
            "expected_read_sha256": expected_read_sha256,
            "selection": "manifest",
            "omitted": ["content"],
        }
    )
