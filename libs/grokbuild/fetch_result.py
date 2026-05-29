"""Post-timeout result retrieval for grokbuild dispatches.

Reads the existing NDJSON sidecar written by the runner. Returns the full
log plus the terminal metadata envelope so callers recover exactly what a
synchronous dispatch would have returned.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal

from build_results import compute_signals, result_ref

from grokbuild.envelope import _envelope_rejected
from grokbuild.events import emit_grok_build_dispatch_rejected
from grokbuild.fetch_result_decode import (
    first_record,
    last_record,
    result_envelope,
    started_metadata,
    summary,
    terminal_age_seconds,
    text_result,
)
from grokbuild.registry import cwds_under
from grokbuild.runner import _SIDECAR_DIR

_VALID_DISPATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_RETENTION_SECONDS = int(
    os.getenv("GROKBUILD_RESULT_RETENTION_SECONDS", str(7 * 24 * 60 * 60))
)


async def fetch_result_op(
    dispatch_id: str,
    format: Literal["json", "text", "summary", "signals"] = "json",
) -> dict[str, Any]:
    """Return the sidecar contents and dispatch metadata for a completed dispatch.

    Missing sidecars reject with ``http_status=404``. Terminal sidecars older
    than the retention window reject with ``http_status=410``. Non-terminal
    sidecars reject as in-flight only when the cwd is still present in the live
    registry.
    """
    if not _valid_dispatch_id(dispatch_id):
        return _reject(
            dispatch_id,
            "invalid_dispatch_id",
            "dispatch_id must be a simple sidecar filename",
            http_status=400,
            result_format=format,
        )

    sidecar_path = _SIDECAR_DIR / f"{dispatch_id}.ndjson"
    if not sidecar_path.exists():
        emit_grok_build_dispatch_rejected(
            dispatch_id=dispatch_id,
            reason_code="result_not_found",
            reason=f"no sidecar for dispatch_id {dispatch_id}",
            mode="read_only",
            op="fetch_result",
            cwd="",
            model="",
        )
        return _reject(
            dispatch_id,
            "result_not_found",
            "sidecar not found",
            http_status=404,
            result_format=format,
        )

    try:
        lines = sidecar_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return _reject(
            dispatch_id,
            "sidecar_read_failed",
            str(exc),
            http_status=500,
            result_format=format,
        )

    records: list[dict[str, Any]] = []
    for line in lines:
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({"raw": line})

    started = first_record(records, "started")
    exit_record = last_record(records, "exit")
    started_meta = started_metadata(started)
    if exit_record is None:
        if started_meta["cwd"] and await cwds_under(started_meta["cwd"]):
            return _reject(
                dispatch_id,
                "dispatch_in_flight",
                "dispatch still running; poll worktree_list until in_flight=false",
                http_status=409,
                result_format=format,
                cwd=started_meta["cwd"],
                model=started_meta["model"],
            )
        return _reject(
            dispatch_id,
            "sidecar_incomplete",
            "sidecar has no terminal exit record and cwd is not in-flight",
            http_status=422,
            result_format=format,
            cwd=started_meta["cwd"],
            model=started_meta["model"],
        )

    terminal_age = terminal_age_seconds(exit_record, str(sidecar_path))
    if _RETENTION_SECONDS > 0 and terminal_age > _RETENTION_SECONDS:
        out = _reject(
            dispatch_id,
            "result_retention_expired",
            "sidecar terminal record is older than the fetch_result retention window",
            http_status=410,
            result_format=format,
            cwd=started_meta["cwd"],
            model=started_meta["model"],
        )
        out["metadata"].update(
            terminal_age_seconds=terminal_age,
            retention_seconds=_RETENTION_SECONDS,
        )
        return out

    out = result_envelope(
        dispatch_id=dispatch_id,
        sidecar_path=str(sidecar_path),
        records=records,
        started_meta=started_meta,
        exit_record=exit_record,
        result_format=format,
        retention_seconds=_RETENTION_SECONDS,
    )
    # The common-channel pointer rides on every successful fetch_result so a
    # caller that fetched the envelope already knows where the fs-reachable
    # spool lives (decision:build-result-common-channel).
    out["result_ref"] = result_ref(dispatch_id)
    if format == "json":
        out["records"] = records
    elif format == "summary":
        out["summary"] = summary(out)
    elif format == "signals":
        # Same dict the worker wrote to signals.json — computed once, here from
        # the just-reconstructed envelope. Bounded; never trips the size guard.
        out["signals"] = compute_signals(out)
    else:  # text
        out["text"] = text_result(out)
    return out


def _valid_dispatch_id(dispatch_id: str) -> bool:
    return bool(_VALID_DISPATCH_ID.fullmatch(dispatch_id))


def _reject(
    dispatch_id: str,
    reason_code: str,
    reason: str,
    *,
    http_status: int,
    result_format: str,
    cwd: str = "",
    model: str = "",
) -> dict[str, Any]:
    out = _envelope_rejected(
        dispatch_id, "read_only", cwd, None, model, reason_code, reason
    )
    out["metadata"].update(http_status=http_status, format=result_format)
    return out
