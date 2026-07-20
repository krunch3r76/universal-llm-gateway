"""Canonical mapping and merge helpers for dispatch economics rollup."""

from __future__ import annotations

import hashlib
from typing import Any

_SUBSTRATE_SDK = "cursor-sdk"
_SUBSTRATE_SNAPSHOT = "stargate-snapshot"
_SUBSTRATE_PIPELINE = "pipeline-frontier"
_SUBSTRATE_CDP = "web-anthropic-cdp"

TOKEN_COLUMNS = (
    "prompt_tokens",
    "completion_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "total_tokens",
)


def int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def rollup_row_id(substrate: str, primary_key: str, signal_seq: int | None) -> str:
    raw = f"{substrate}:{primary_key}:{signal_seq or 0}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def map_sdk_row(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    usage = payload.get("usage") or {}
    execution_id = payload.get("execution_id") or row.get("execution_id")
    dispatch_id = payload.get("dispatch_id")
    primary_key = execution_id or dispatch_id or ""
    status = str(payload.get("usage_capture_status") or "missing")
    return {
        "substrate": _SUBSTRATE_SDK,
        "primary_key": primary_key,
        "execution_id": execution_id,
        "dispatch_id": dispatch_id,
        "thread_id": payload.get("thread_id"),
        "model_id": payload.get("resolved_model"),
        "signal_seq": row.get("seq"),
        "usage_capture_status": status,
        "prompt_tokens": int_or_none(usage.get("input_tokens")),
        "completion_tokens": int_or_none(usage.get("output_tokens")),
        "cache_read_tokens": int_or_none(usage.get("cache_read_tokens")),
        "cache_write_tokens": int_or_none(usage.get("cache_write_tokens")),
        "total_tokens": int_or_none(usage.get("total_tokens")),
        "rollup_row_id": rollup_row_id(_SUBSTRATE_SDK, primary_key, row.get("seq")),
    }


def map_snapshot_row(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    usage = payload.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    request_id = payload.get("request_id") or row.get("request_id") or ""
    has_usage = bool(usage)
    return {
        "substrate": _SUBSTRATE_SNAPSHOT,
        "primary_key": request_id,
        "execution_id": payload.get("execution_id") or row.get("execution_id"),
        "dispatch_id": payload.get("dispatch_id"),
        "thread_id": payload.get("thread_id"),
        "model_id": payload.get("model_id"),
        "signal_seq": row.get("seq"),
        "usage_capture_status": "captured" if has_usage else "missing",
        "prompt_tokens": int_or_none(usage.get("prompt_tokens")),
        "completion_tokens": int_or_none(usage.get("completion_tokens")),
        "cache_read_tokens": int_or_none(details.get("cached_tokens")),
        "cache_write_tokens": None,
        "total_tokens": int_or_none(usage.get("total_tokens")),
        "rollup_row_id": rollup_row_id(_SUBSTRATE_SNAPSHOT, request_id, row.get("seq")),
    }


def map_pipeline_row(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    execution_id = payload.get("execution_id") or row.get("execution_id") or ""
    prompt = int_or_none(payload.get("prompt_tokens"))
    completion = int_or_none(payload.get("completion_tokens"))
    total = prompt + completion if prompt is not None and completion is not None else None
    return {
        "substrate": _SUBSTRATE_PIPELINE,
        "primary_key": execution_id,
        "execution_id": execution_id or None,
        "dispatch_id": payload.get("dispatch_id"),
        "thread_id": payload.get("thread_id"),
        "model_id": payload.get("model_entity_id") or payload.get("model"),
        "signal_seq": row.get("seq"),
        "usage_capture_status": "captured",
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cache_read_tokens": int_or_none(payload.get("cached_tokens")),
        "cache_write_tokens": None,
        "total_tokens": total,
        "rollup_row_id": rollup_row_id(_SUBSTRATE_PIPELINE, execution_id, row.get("seq")),
    }


def map_cdp_stub(*, execution_id: str, archived_at: str | None) -> dict[str, Any]:
    return {
        "substrate": _SUBSTRATE_CDP,
        "primary_key": execution_id,
        "execution_id": execution_id,
        "dispatch_id": None,
        "thread_id": None,
        "model_id": None,
        "signal_seq": None,
        "usage_capture_status": "unavailable",
        "prompt_tokens": None,
        "completion_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "total_tokens": None,
        "archived_at": archived_at,
        "rollup_row_id": rollup_row_id(_SUBSTRATE_CDP, execution_id, None),
    }


def source_priority(row: dict[str, Any]) -> int:
    substrate = row["substrate"]
    if substrate == _SUBSTRATE_SDK:
        if row.get("usage_capture_status") == "captured":
            return 3
        return 1
    if substrate == _SUBSTRATE_PIPELINE:
        return 2
    if substrate == _SUBSTRATE_SNAPSHOT:
        return 0
    return -1


def merge_token_column(rows: list[dict[str, Any]], column: str) -> tuple[Any, bool]:
    ranked = sorted(rows, key=source_priority, reverse=True)
    chosen = None
    conflict = False
    for row in ranked:
        value = row.get(column)
        if value is None:
            continue
        if chosen is None:
            chosen = value
        elif chosen != value:
            conflict = True
    return chosen, conflict


def coalesce_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = sorted(rows, key=source_priority, reverse=True)
    base = dict(ranked[0])
    merge_conflict = False
    conflict_vectors: dict[str, dict[str, Any]] = {}
    for column in TOKEN_COLUMNS:
        merged, conflict = merge_token_column(rows, column)
        base[column] = merged
        if conflict:
            merge_conflict = True
            conflict_vectors[column] = {
                row["substrate"]: row.get(column)
                for row in rows
                if row.get(column) is not None
            }
    base["usage_capture_status"] = next(
        (row["usage_capture_status"] for row in ranked if row.get("usage_capture_status")),
        "missing",
    )
    sdk_row = next((row for row in rows if row["substrate"] == _SUBSTRATE_SDK), None)
    if sdk_row:
        for field in ("dispatch_id", "thread_id"):
            if sdk_row.get(field):
                base[field] = sdk_row[field]
    base["join_quality"] = "coalesced"
    base["merge_conflict"] = merge_conflict
    if merge_conflict:
        base["conflict_vectors"] = conflict_vectors
    base["source_substrates"] = sorted({row["substrate"] for row in rows})
    base["rollup_row_id"] = rollup_row_id(
        "coalesced",
        str(base.get("execution_id") or base.get("primary_key")),
        base.get("signal_seq"),
    )
    return base


def comparable_total(row: dict[str, Any]) -> int | None:
    prompt = row.get("prompt_tokens")
    completion = row.get("completion_tokens")
    if prompt is not None and completion is not None:
        return prompt + completion
    return None


def is_orphan(row: dict[str, Any]) -> bool:
    substrate = row["substrate"]
    if substrate == _SUBSTRATE_CDP:
        return False
    if substrate in {_SUBSTRATE_SDK, _SUBSTRATE_PIPELINE}:
        return not row.get("execution_id")
    if substrate == _SUBSTRATE_SNAPSHOT:
        return not row.get("primary_key")
    return False
