"""Centralized envelope-to-HTTP-status mapping for grokbuild-worker routes.

The ``libs/grokbuild`` functions never raise — they return envelope dicts
with ``status`` ∈ {completed, rejected, failed} and ``metadata.reason_code``.
``raise_if_error`` inspects the envelope and raises ``HTTPException`` when
the status is not ``completed``, keeping all HTTP-status decisions in one
place and out of individual route handlers.

HTTP status ladder:
* ``completed``  → no exception (200)
* ``rejected``   → 4xx keyed by reason_code; fallback 400
* ``failed``     → 502 (external service failure) or 500 (internal sidecar)
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

# Reason codes that map to 404 (resource absent).
_NOT_FOUND: frozenset[str] = frozenset(
    {"worktree_not_found", "result_not_found", "not_a_git_repo"}
)

# Reason codes that map to 409 (conflict / resource busy).
_CONFLICT: frozenset[str] = frozenset(
    {
        "worktree_exists",
        "branch_checked_out_elsewhere",
        "worktree_dirty",
        "worktree_busy",
        "dispatch_in_flight",
    }
)

# Reason codes that map to 410 (gone / retired).
_GONE: frozenset[str] = frozenset(
    {"retired_op", "retired_output_format", "retired_param", "result_retention_expired"}
)

# Reason codes that map to 422 (unprocessable — structurally valid but semantically unusable).
_UNPROCESSABLE: frozenset[str] = frozenset({"sidecar_incomplete"})

# Reason codes that map to 500 (internal sidecar error).
_INTERNAL: frozenset[str] = frozenset({"sidecar_read_failed"})


def _reason_to_status(reason_code: str, metadata: dict[str, Any]) -> int:
    """Resolve HTTP status from reason_code.

    fetch_result_op embeds ``http_status`` in metadata — prefer it when
    present so we don't duplicate its own mapping logic.
    """
    if "http_status" in metadata:
        return int(metadata["http_status"])
    if reason_code in _NOT_FOUND:
        return 404
    if reason_code in _CONFLICT:
        return 409
    if reason_code in _GONE:
        return 410
    if reason_code in _UNPROCESSABLE:
        return 422
    if reason_code in _INTERNAL:
        return 500
    return 400


def raise_if_error(envelope: dict[str, Any]) -> None:
    """Raise ``HTTPException`` when the envelope indicates an error.

    No-op for ``status="completed"`` — routes continue normally after
    calling this. For rejected/failed, the HTTPException detail carries
    the structured reason so callers can decode the failure.
    """
    status = envelope.get("status", "")
    if status == "completed":
        return

    meta: dict[str, Any] = envelope.get("metadata", {})
    reason_code: str = meta.get("reason_code", "")
    reason: str = meta.get("reason", "")

    if status == "rejected":
        http_status = _reason_to_status(reason_code, meta)
        raise HTTPException(
            status_code=http_status,
            detail={"reason_code": reason_code, "reason": reason},
        )

    # status == "failed": external service / subprocess failure.
    if reason_code in _INTERNAL:
        http_status = 500
    else:
        http_status = 502
    raise HTTPException(
        status_code=http_status,
        detail={
            "reason_code": reason_code or "op_failed",
            "reason": reason or "operation failed",
        },
    )
