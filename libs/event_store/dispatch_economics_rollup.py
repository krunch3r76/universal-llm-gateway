"""Query-time cross-substrate dispatch token economics rollup."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from .dispatch_economics_core import (
    TOKEN_COLUMNS,
    coalesce_group,
    comparable_total,
    is_orphan,
    map_cdp_stub,
    map_pipeline_row,
    map_sdk_row,
    map_snapshot_row,
)
from .operation_parameters import (
    _coerce_minutes,
    _coerce_since_ts,
    _get_session_start_ts,
)
from .store import EventStore

logger = get_logger(__name__)

_SDK_SIGNAL = "frontier.sdk.worker.completed"
_SNAPSHOT_SIGNAL = "request.snapshot.completed"
_PIPELINE_SIGNAL = "pipeline.frontier.dispatch.completed"

_SUBSTRATE_SDK = "cursor-sdk"
_SUBSTRATE_SNAPSHOT = "stargate-snapshot"
_SUBSTRATE_PIPELINE = "pipeline-frontier"
_SUBSTRATE_CDP = "web-anthropic-cdp"

_ARCHIVED_AT_RE = re.compile(r"^- archived_at: `([^`]+)`", re.M)
_EXECUTION_ID_RE = re.compile(r"^- execution_id: `([^`]+)`", re.M)


def _parse_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_eid: dict[str, list[dict[str, Any]]] = {}
    standalone: list[dict[str, Any]] = []
    for row in rows:
        eid = row.get("execution_id")
        if eid:
            by_eid.setdefault(str(eid), []).append(row)
        else:
            tagged = dict(row)
            tagged["join_quality"] = "orphan" if is_orphan(row) else "standalone"
            tagged["merge_conflict"] = False
            tagged["comparable_total_tokens"] = comparable_total(row)
            standalone.append(tagged)

    merged: list[dict[str, Any]] = []
    for group in by_eid.values():
        if len(group) == 1:
            only = dict(group[0])
            only["join_quality"] = "standalone"
            only["merge_conflict"] = False
            only["comparable_total_tokens"] = comparable_total(only)
            merged.append(only)
        else:
            coalesced = coalesce_group(group)
            coalesced["comparable_total_tokens"] = comparable_total(coalesced)
            merged.append(coalesced)
    return merged + standalone


def _join_audit(
    raw_rows: list[dict[str, Any]],
    output_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    eid_substrates: dict[str, set[str]] = {}
    for row in raw_rows:
        eid = row.get("execution_id")
        if not eid:
            continue
        eid_substrates.setdefault(str(eid), set()).add(row["substrate"])
    total_eids = len(eid_substrates)
    double_count = sum(1 for subs in eid_substrates.values() if len(subs) > 1)
    double_count_rate = (double_count / total_eids) if total_eids else 0.0

    cdp_stub_count = sum(1 for row in output_rows if row["substrate"] == _SUBSTRATE_CDP)
    sdk_pipeline_rows = [
        row
        for row in output_rows
        if row["substrate"] in {_SUBSTRATE_SDK, _SUBSTRATE_PIPELINE}
    ]
    orphan_count = sum(1 for row in sdk_pipeline_rows if row.get("join_quality") == "orphan")
    orphan_denom = len(sdk_pipeline_rows)
    orphan_rate = (orphan_count / orphan_denom) if orphan_denom else 0.0
    merge_conflict_count = sum(1 for row in output_rows if row.get("merge_conflict"))

    return {
        "double_count_rate": round(double_count_rate, 6),
        "orphan_rate": round(orphan_rate, 6),
        "merge_conflict_count": merge_conflict_count,
        "cdp_stub_count": cdp_stub_count,
        "execution_id_count": total_eids,
        "double_count_execution_ids": double_count,
        "orphan_count": orphan_count,
    }


def _coverage_bucket(status: str | None) -> str:
    if status == "captured":
        return "captured"
    if status == "unavailable":
        return "unavailable"
    return "missing"


def _build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {"row_count": len(rows)}
    comparable_total_sum = 0
    comparable_rows = 0
    for column in TOKEN_COLUMNS:
        total = 0
        coverage = {"captured": 0, "missing": 0, "unavailable": 0}
        has_any = False
        for row in rows:
            value = row.get(column)
            bucket = _coverage_bucket(row.get("usage_capture_status"))
            if value is not None:
                total += value
                has_any = True
            coverage[bucket] += 1
        summary[column] = total if has_any else None
        summary[f"{column}_coverage"] = coverage
    for row in rows:
        comp = row.get("comparable_total_tokens")
        if comp is not None:
            comparable_total_sum += comp
            comparable_rows += 1
    summary["comparable_total_tokens"] = comparable_total_sum if comparable_rows else None
    summary["comparable_total_tokens_coverage"] = {
        "captured": comparable_rows,
        "missing": len(rows) - comparable_rows,
        "unavailable": sum(
            1 for row in rows if row.get("usage_capture_status") == "unavailable"
        ),
    }
    return summary


def _parse_archive_timestamp(raw: str) -> int | None:
    try:
        normalized = raw.replace("Z", "+00:00")
        return int(datetime.fromisoformat(normalized).timestamp() * 1000)
    except ValueError:
        return None


def discover_cdp_stubs(since_ts: int, until_ts: int | None) -> list[dict[str, Any]]:
    root_raw = os.environ.get("CORTEX_FILES_ROOT", "").strip()
    if not root_raw:
        return []
    threads = Path(root_raw).expanduser() / "notes" / "system" / "threads"
    if not threads.is_dir():
        return []
    stubs: list[dict[str, Any]] = []
    for path in threads.glob("cdp-ask-archive*.md"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        exec_match = _EXECUTION_ID_RE.search(text)
        if not exec_match:
            continue
        archived_at = None
        archived_ts = None
        archived_match = _ARCHIVED_AT_RE.search(text)
        if archived_match:
            archived_at = archived_match.group(1)
            archived_ts = _parse_archive_timestamp(archived_at)
        if archived_ts is not None:
            if archived_ts < since_ts:
                continue
            if until_ts is not None and archived_ts > until_ts:
                continue
        stubs.append(map_cdp_stub(execution_id=exec_match.group(1), archived_at=archived_at))
    return stubs


async def _resolve_window(
    params: dict[str, Any],
    store: EventStore,
) -> tuple[int, int | None, int | None]:
    since_ts = _coerce_since_ts(params.get("since_ts"))
    until_ts = _coerce_since_ts(params.get("until_ts"))
    minutes = _coerce_minutes(params.get("minutes"))
    if since_ts is None:
        if minutes is not None:
            since_ts = int(datetime.now(tz=UTC).timestamp() * 1000) - minutes * 60_000
        else:
            since_ts = await _get_session_start_ts(store)
    if since_ts is None:
        since_ts = int(datetime.now(tz=UTC).timestamp() * 1000) - 24 * 60 * 60_000
    return since_ts, until_ts, minutes


async def _query_signal_rows(
    store: EventStore,
    *,
    signal: str,
    since_ts: int,
    until_ts: int | None,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    sql = (
        "SELECT seq, signal, execution_id, request_id, ts_unix_ms, payload "
        "FROM events WHERE signal = ? AND ts_unix_ms >= ?"
    )
    query_params: list[Any] = [signal, since_ts]
    if until_ts is not None:
        sql += " AND ts_unix_ms <= ?"
        query_params.append(until_ts)
    execution_id = params.get("execution_id")
    if execution_id:
        sql += " AND execution_id = ?"
        query_params.append(execution_id)
    request_id = params.get("request_id")
    if request_id and signal == _SNAPSHOT_SIGNAL:
        sql += " AND request_id = ?"
        query_params.append(request_id)
    dispatch_id = params.get("dispatch_id")
    if dispatch_id and signal == _SDK_SIGNAL:
        sql += " AND dispatch_id = ?"
        query_params.append(dispatch_id)
    sql += " ORDER BY seq DESC LIMIT 5000"
    rows = await store.query(sql, tuple(query_params))
    return [{**dict(row), "payload": _parse_payload(row)} for row in rows]


def _apply_seat_substrate_filter(
    rows: list[dict[str, Any]],
    seat_substrate: str | None,
) -> list[dict[str, Any]]:
    if not seat_substrate:
        return rows
    normalized = seat_substrate.strip().lower()
    aliases = {
        "cursor-sdk": _SUBSTRATE_SDK,
        "sdk": _SUBSTRATE_SDK,
        "cursor": _SUBSTRATE_SDK,
        "stargate-snapshot": _SUBSTRATE_SNAPSHOT,
        "snapshot": _SUBSTRATE_SNAPSHOT,
        "stargate": _SUBSTRATE_SNAPSHOT,
        "pipeline-frontier": _SUBSTRATE_PIPELINE,
        "pipeline": _SUBSTRATE_PIPELINE,
        "web-anthropic-cdp": _SUBSTRATE_CDP,
        "cdp": _SUBSTRATE_CDP,
    }
    target = aliases.get(normalized, normalized)
    return [row for row in rows if row.get("substrate") == target]


def build_dispatch_economics_rollup(
    *,
    sdk_rows: list[dict[str, Any]],
    snapshot_rows: list[dict[str, Any]],
    pipeline_rows: list[dict[str, Any]],
    cdp_stubs: list[dict[str, Any]],
    seat_substrate: str | None = None,
) -> dict[str, Any]:
    mapped = (
        [map_sdk_row(row, row["payload"]) for row in sdk_rows]
        + [map_snapshot_row(row, row["payload"]) for row in snapshot_rows]
        + [map_pipeline_row(row, row["payload"]) for row in pipeline_rows]
    )
    known_eids = {str(row["execution_id"]) for row in mapped if row.get("execution_id")}
    raw_rows = mapped + [
        stub for stub in cdp_stubs if stub.get("execution_id") not in known_eids
    ]
    output_rows = _apply_seat_substrate_filter(_dedupe_rows(raw_rows), seat_substrate)
    return {
        "rows": output_rows,
        "summary": _build_summary(output_rows),
        "join_audit": _join_audit(raw_rows, output_rows),
    }


async def dispatch_economics_token_rollup(
    params: dict[str, Any],
    store: EventStore,
) -> dict[str, Any]:
    since_ts, until_ts, minutes = await _resolve_window(params, store)
    sdk_rows = await _query_signal_rows(
        store, signal=_SDK_SIGNAL, since_ts=since_ts, until_ts=until_ts, params=params
    )
    snapshot_rows = await _query_signal_rows(
        store,
        signal=_SNAPSHOT_SIGNAL,
        since_ts=since_ts,
        until_ts=until_ts,
        params=params,
    )
    pipeline_rows = await _query_signal_rows(
        store,
        signal=_PIPELINE_SIGNAL,
        since_ts=since_ts,
        until_ts=until_ts,
        params=params,
    )
    cdp_stubs = discover_cdp_stubs(since_ts, until_ts)
    body = build_dispatch_economics_rollup(
        sdk_rows=sdk_rows,
        snapshot_rows=snapshot_rows,
        pipeline_rows=pipeline_rows,
        cdp_stubs=cdp_stubs,
        seat_substrate=params.get("seat_substrate"),
    )
    body["window"] = {"since_ts": since_ts, "until_ts": until_ts, "minutes": minutes}
    body["source_counts"] = {
        "cursor-sdk": len(sdk_rows),
        "stargate-snapshot": len(snapshot_rows),
        "pipeline-frontier": len(pipeline_rows),
        "web-anthropic-cdp": len(cdp_stubs),
    }
    return body
