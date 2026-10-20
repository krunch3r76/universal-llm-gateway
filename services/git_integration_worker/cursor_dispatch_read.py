"""Read-only cursor dispatch ledger projections for HTTP routes."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from event_store.dispatch_economics_pricing import _price_row, wire_usd_present

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest import (
    consult_pending_continue_owed,
)
from services.git_integration_worker.cursor_sdk_closeout_harvest import (
    _VERDICT_LINE_RE,
    _unfenced_line_starts,
    _unique_line,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    _SIDECAR_DIR,
)
from services.git_integration_worker.cursor_sdk_ledger_hop import (
    hop_fields_from_record_json,
)
from services.git_integration_worker.cursor_sdk_usage_extract import (
    read_persisted_usage,
)
from services.git_integration_worker.relay.closeout_relay_common import (
    extract_structured_closeout_full_text,
)
from services.git_integration_worker.relay.episode_residue import (
    changed_paths_from_closeout,
)

_BUDGET_BYTES = 5120
_MAX_VERDICT_BYTES = 160
_MAX_PATH_SAMPLE = 8
_LIVE_STATUSES = ("queued", "admitted", "running", "parked_waiting")
_CLAIM_CONTRACT = "claim"
_MILESTONE_TURN_IDS_REASON = (
    "milestone_turn_ids not projected on work-key ledger reads"
)


def _is_conductor_row(row: dict[str, Any]) -> bool:
    from services.git_integration_worker.cursor_sdk_conductor_identity import (
        is_conductor_dispatch_row,
    )

    return is_conductor_dispatch_row(row)


def _record_json_dict(row: dict[str, Any]) -> dict[str, Any]:
    raw = str(row.get("record_json") or "")
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def _clamp_limit(limit: int) -> int:
    return max(1, min(10, int(limit)))


def _clamp_window_hours(window_hours: int) -> int:
    return max(1, min(168, int(window_hours)))


def _sort_key(row: dict[str, Any]) -> str:
    return str(row.get("started_at") or row.get("queued_at") or "")


def _window_cutoff_iso(window_hours: int, *, now: datetime | None = None) -> str:
    stamp = now or datetime.now(UTC)
    return (stamp - timedelta(hours=window_hours)).isoformat()


def _compact_json_bytes(payload: dict[str, Any]) -> int:
    return len(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )


def _parent_or_nest(row: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    hop = hop_fields_from_record_json(str(row.get("record_json") or ""))
    out: dict[str, Any] = {}
    for key, src in (
        ("nest_under", row.get("nest_under") or rec.get("nest_under")),
        ("resume_of", row.get("resume_of") or rec.get("resume_of")),
        ("hop_from", row.get("hop_from") or hop.get("hop_from") or rec.get("hop_from")),
        (
            "park_child_dispatch_id",
            row.get("park_child_dispatch_id") or rec.get("park_child_dispatch_id"),
        ),
    ):
        if src is not None and str(src).strip():
            out[key] = str(src)
    return out


def _knobs_from_row(row: dict[str, Any], rec: dict[str, Any]) -> dict[str, str] | None:
    raw = rec.get("model_knobs") if rec.get("model_knobs") is not None else None
    if isinstance(raw, dict) and raw:
        return {str(k): str(v) for k, v in raw.items()}
    return None


def _usage_pricing_row(
    usage: dict[str, Any],
    *,
    model: str,
    knobs: dict[str, str] | None,
) -> dict[str, Any]:
    return {
        "prompt_tokens": usage.get("input_tokens"),
        "completion_tokens": usage.get("output_tokens"),
        "cache_read_tokens": usage.get("cache_read_tokens"),
        "cache_write_tokens": usage.get("cache_write_tokens"),
        "total_tokens_basis": usage.get("total_tokens_basis"),
        "model_id": model,
        "model_knobs_requested": knobs,
    }


def _project_usage_cost(
    row: dict[str, Any],
    rec: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    model = str(row.get("resolved_model") or "")
    knobs = _knobs_from_row(row, rec)
    persisted = read_persisted_usage(str(row.get("record_json") or ""))
    if persisted is None or persisted.usage is None:
        return None, "no persisted usage on ledger row"
    usage = dict(persisted.usage)
    status = str(persisted.usage_capture_status or "missing")
    wire_usd, _wire_key = wire_usd_present(usage, status)
    priced = _price_row(_usage_pricing_row(usage, model=model, knobs=knobs), None)
    out: dict[str, Any] = {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "cache_read_tokens": usage.get("cache_read_tokens"),
        "cache_write_tokens": usage.get("cache_write_tokens"),
        "cost_usd": priced.get("cost_usd"),
        "cost_source": priced.get("cost_source"),
    }
    if wire_usd is not None and priced.get("cost_source") == "wire":
        out["cost_usd"] = wire_usd
        out["cost_source"] = "wire"
    return out, None


def _read_sidecar_text(row: dict[str, Any]) -> str | None:
    dispatch_id = str(row.get("dispatch_id") or "")
    source_repo = row.get("source_repo")
    if not dispatch_id or not source_repo:
        return None
    path = Path(str(source_repo)) / _SIDECAR_DIR / f"{dispatch_id}.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _structured_closeout(row: dict[str, Any]) -> dict[str, Any] | None:
    sidecar = _read_sidecar_text(row)
    if sidecar is None:
        return None
    raw = extract_structured_closeout_full_text(sidecar)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _verdict_from_closeout(row: dict[str, Any], structured: dict[str, Any] | None) -> str | None:
    candidates: list[str] = []
    if structured is not None:
        for key in ("closeout_body", "verdict"):
            val = structured.get(key)
            if isinstance(val, str) and val.strip():
                candidates.append(val)
    rec = _record_json_dict(row)
    body = rec.get("closeout_body")
    if isinstance(body, str) and body.strip():
        candidates.append(body)
    sidecar = _read_sidecar_text(row)
    if sidecar:
        candidates.append(sidecar)
    for content in candidates:
        line = _unique_line(_unfenced_line_starts(_VERDICT_LINE_RE, content))
        if not line:
            continue
        encoded = line.encode("utf-8")
        if len(encoded) <= _MAX_VERDICT_BYTES:
            return line
        return encoded[:_MAX_VERDICT_BYTES].decode("utf-8", errors="ignore")
    return None


def _land_from_closeout(structured: dict[str, Any] | None) -> dict[str, Any]:
    if structured is None:
        return {"head_sha": None, "landed": None}
    head = structured.get("head_sha")
    landed = structured.get("landed")
    return {
        "head_sha": head if isinstance(head, str) else None,
        "landed": landed if isinstance(landed, bool) else None,
    }


def _sha_closeout_from_structured(structured: dict[str, Any] | None) -> str | None:
    if structured is None:
        return None
    propagation = structured.get("propagation")
    if not isinstance(propagation, list):
        return None
    for row in propagation:
        if not isinstance(row, dict):
            continue
        emission = row.get("liveness_emission")
        if not isinstance(emission, dict):
            continue
        observed = emission.get("observed_code_version")
        if isinstance(observed, str) and observed.strip():
            return observed.strip()
    return None


def _paths_from_structured(structured: dict[str, Any] | None) -> dict[str, Any] | None:
    if structured is None:
        return None
    try:
        raw = json.dumps(structured, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    parsed = changed_paths_from_closeout(raw)
    if parsed is None:
        return {"total_count": 0, "sample": [], "omitted_count": 0}
    paths, truncated = parsed
    total = len(paths)
    sample = list(paths[:_MAX_PATH_SAMPLE])
    return {
        "total_count": total,
        "sample": sample,
        "omitted_count": max(0, total - len(sample)),
        "truncated": truncated,
    }


def _project_dispatch_row(row: dict[str, Any]) -> dict[str, Any]:
    rec = _record_json_dict(row)
    structured = _structured_closeout(row)
    usage_cost, usage_reason = _project_usage_cost(row, rec)
    parent = _parent_or_nest(row, rec)
    out: dict[str, Any] = {
        "dispatch_id": row.get("dispatch_id"),
        "execution_id": row.get("execution_id"),
        "model": row.get("resolved_model"),
        "knobs": _knobs_from_row(row, rec),
        "contract": row.get("contract"),
        "admit_at": row.get("queued_at") or row.get("started_at"),
        "terminal_at": row.get("terminal_at"),
        "status": row.get("status"),
        "usage_cost": usage_cost,
        "verdict": _verdict_from_closeout(row, structured),
        "land": _land_from_closeout(structured),
        "milestone_turn_ids_reason": _MILESTONE_TURN_IDS_REASON,
    }
    if usage_reason is not None:
        out["usage_cost_absent_reason"] = usage_reason
    if parent:
        out["parent_or_nest"] = parent
    sha_closeout = _sha_closeout_from_structured(structured)
    out["sha_closeout"] = sha_closeout
    paths = _paths_from_structured(structured)
    if paths is not None:
        out["paths"] = paths
    return out


def _live_dispatches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    live: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") not in _LIVE_STATUSES:
            continue
        if str(row.get("contract") or "") == _CLAIM_CONTRACT:
            continue
        live.append(
            {
                "dispatch_id": row.get("dispatch_id"),
                "status": row.get("status"),
                "model": row.get("resolved_model"),
            }
        )
    return live


def _model_mix_24h(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str, str], int] = {}
    for row in rows:
        if str(row.get("contract") or "") == _CLAIM_CONTRACT:
            continue
        rec = _record_json_dict(row)
        knobs = _knobs_from_row(row, rec)
        key = (
            str(row.get("resolved_model") or ""),
            json.dumps(knobs or {}, sort_keys=True, separators=(",", ":")),
            str(row.get("contract") or ""),
        )
        counts[key] = counts.get(key, 0) + 1
    mix: list[dict[str, Any]] = []
    for (model, knobs_json, contract), count in sorted(
        counts.items(), key=lambda item: (-item[1], item[0][0])
    ):
        knobs_obj = json.loads(knobs_json) if knobs_json else None
        mix.append(
            {
                "model": model,
                "knobs": knobs_obj,
                "contract": contract,
                "count": count,
            }
        )
    return mix


def _usage_24h(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    cost_usd = 0.0
    has_cost = False
    covered = 0
    total = 0
    for row in rows:
        if str(row.get("contract") or "") == _CLAIM_CONTRACT:
            continue
        total += 1
        rec = _record_json_dict(row)
        usage_cost, _reason = _project_usage_cost(row, rec)
        if usage_cost is None:
            continue
        covered += 1
        for key in totals:
            val = usage_cost.get(key)
            if isinstance(val, int):
                totals[key] += val
        cusd = usage_cost.get("cost_usd")
        if isinstance(cusd, (int, float)):
            cost_usd += float(cusd)
            has_cost = True
    body: dict[str, Any] = {
        **totals,
        "cost_usd": round(cost_usd, 8) if has_cost else None,
        "rows_covered": covered,
        "rows_total": total,
    }
    return body


def _enforce_5kb_budget(payload: dict[str, Any]) -> dict[str, Any]:
    dispatches = list(payload.get("dispatches") or [])
    total_dispatches = int(payload.get("total_dispatches") or len(dispatches))
    truncated = False

    def fits() -> bool:
        return _compact_json_bytes(payload) <= _BUDGET_BYTES

    if not fits():
        truncated = True
        for idx in range(len(dispatches) - 1, -1, -1):
            row = dispatches[idx]
            paths = row.get("paths")
            if isinstance(paths, dict) and paths.get("sample"):
                paths = dict(paths)
                paths["sample"] = []
                paths["omitted_count"] = int(paths.get("total_count") or 0)
                row = dict(row)
                row["paths"] = paths
                dispatches[idx] = row
                payload = {**payload, "dispatches": dispatches}
                if fits():
                    break

    while not fits() and len(dispatches) > 1:
        truncated = True
        dispatches = dispatches[:-1]
        payload = {**payload, "dispatches": dispatches}

    actual = _compact_json_bytes(payload)
    payload["budget"] = {
        "actual_bytes": actual,
        "truncated": truncated,
        "returned_dispatches": len(dispatches),
        "total_dispatches": total_dispatches,
        "omitted_dispatches": max(0, total_dispatches - len(dispatches)),
        "max_bytes": _BUDGET_BYTES,
    }
    return payload


def work_key_ledger_projection(
    work_key: str,
    *,
    limit: int = 5,
    window_hours: int = 24,
    ledger: CursorDispatchLedger | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read-only work-key ledger for ``GET /work-key-ledger``."""
    led = ledger or CursorDispatchLedger.instance()
    limit_i = _clamp_limit(limit)
    window_i = _clamp_window_hours(window_hours)
    cutoff = _window_cutoff_iso(window_i, now=now)

    with led._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE work_key=? "
            "AND COALESCE(contract,'') != ?",
            (work_key, _CLAIM_CONTRACT),
        ).fetchall()
    mapped = [_row_to_dict(row) for row in rows]
    in_window = [row for row in mapped if _sort_key(row) >= cutoff]
    in_window.sort(key=_sort_key, reverse=True)
    total_dispatches = len(in_window)
    window_rows = list(in_window)
    projected = [_project_dispatch_row(row) for row in in_window[:limit_i]]

    live = _live_dispatches(mapped)
    payload: dict[str, Any] = {
        "work_key": work_key,
        "limit": limit_i,
        "window_hours": window_i,
        "dispatches": projected,
        "total_dispatches": total_dispatches,
        "house_rollup": {
            "live_dispatches": live,
            "live_count": len(live),
            "model_mix_24h": _model_mix_24h(window_rows),
            "usage_24h": _usage_24h(window_rows),
        },
    }
    return _enforce_5kb_budget(payload)


def latest_terminal_conductor_row_for_thread(
    ledger: CursorDispatchLedger,
    thread_id: str,
) -> dict[str, Any] | None:
    """Return the latest terminal conductor row on *thread_id*, or None."""
    best: dict[str, Any] | None = None
    best_seq = -1
    with ledger._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches "
            "WHERE thread_id=? AND status IN ('completed','failed','cancelled')",
            (thread_id,),
        ).fetchall()
    for row in rows:
        mapped = {k: row[k] for k in row.keys()}
        if not _is_conductor_row(mapped):
            continue
        hop_seq = hop_fields_from_record_json(str(mapped.get("record_json") or "")).get(
            "hop_seq"
        )
        seq_i = int(hop_seq) if isinstance(hop_seq, int) else 0
        if seq_i >= best_seq:
            best_seq = seq_i
            best = mapped
    return best


def occupancy_projection(
    work_key: str,
    *,
    now: str | None = None,
    ledger: CursorDispatchLedger | None = None,
) -> dict[str, Any]:
    """Thin read of house occupancy for ``GET /occupancy``.

    Delegates to the claim module. The label is occupancy, not progress:
    the payload has no landed, done, or progress fields.
    """
    from services.git_integration_worker.cursor_sdk_work_claim import occupancy

    return occupancy(work_key, now, ledger=ledger)


def latest_terminal_conductor_api_payload(
    *,
    thread_id: str,
    ledger: CursorDispatchLedger | None = None,
) -> dict[str, Any] | None:
    """Shape a terminal conductor row for ``GET .../latest-terminal-conductor``."""
    ledger = ledger or CursorDispatchLedger.instance()
    row = latest_terminal_conductor_row_for_thread(ledger, thread_id)
    if row is None:
        return None
    rec = _record_json_dict(row)
    hop = hop_fields_from_record_json(str(row.get("record_json") or ""))
    closeout_body = rec.get("closeout_body") or row.get("closeout_body") or ""
    if not isinstance(closeout_body, str):
        closeout_body = str(closeout_body)
    scoreboard_uri = str(
        rec.get("scoreboard_uri") or rec.get("scoreboard") or ""
    ).strip()
    closeout_turn = rec.get("closeout_turn")
    owed = consult_pending_continue_owed(row)
    return {
        "dispatch_id": row.get("dispatch_id"),
        "status": row.get("status"),
        "thread_id": row.get("thread_id"),
        "hop_seq": hop.get("hop_seq"),
        "record_json": rec,
        "closeout_body": closeout_body,
        "scoreboard_uri": scoreboard_uri,
        "closeout_turn": closeout_turn,
        "consult_pending_continue_owed": owed,
    }


__all__ = [
    "latest_terminal_conductor_api_payload",
    "latest_terminal_conductor_row_for_thread",
    "occupancy_projection",
    "work_key_ledger_projection",
]
