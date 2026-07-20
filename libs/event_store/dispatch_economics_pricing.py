"""Query-time dollar-equivalent join atop G2 dispatch token rollup."""

from __future__ import annotations

import math
from typing import Any, Literal

from .dispatch_economics_core import (
    _SUBSTRATE_PIPELINE,
    _SUBSTRATE_SDK,
    _SUBSTRATE_SNAPSHOT,
)
from .dispatch_economics_rollup import (
    _PIPELINE_SIGNAL,
    _SDK_SIGNAL,
    _SNAPSHOT_SIGNAL,
    _query_signal_rows,
    _resolve_window,
    dispatch_economics_token_rollup,
)
from .model_rate_table import ModelRateRow, resolve_rate
from .store import EventStore

CostSource = Literal["wire", "rate_x_tokens", "unavailable"]

_WIRE_USD_KEYS = ("cost_usd", "spend", "cost")
_WIRE_SUBSTRATE_PRIORITY = {
    _SUBSTRATE_SDK: 3,
    _SUBSTRATE_PIPELINE: 2,
    _SUBSTRATE_SNAPSHOT: 1,
}


def _finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def wire_usd_present(
    usage: dict[str, Any],
    usage_capture_status: str | None,
) -> tuple[float | None, str | None]:
    """Return authoritative wire USD amount and key when present (F1)."""
    for key in _WIRE_USD_KEYS:
        if key not in usage:
            continue
        amount = _finite_float(usage[key])
        if amount is None:
            continue
        if amount > 0:
            return amount, key
        if amount == 0.0 and usage_capture_status == "captured":
            return 0.0, key
    return None, None


def _member_usage(
    substrate: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    if substrate == _SUBSTRATE_SDK:
        usage = payload.get("usage") or {}
        status = str(payload.get("usage_capture_status") or "missing")
        return usage if isinstance(usage, dict) else {}, status
    if substrate == _SUBSTRATE_SNAPSHOT:
        usage = payload.get("usage") or {}
        status = "captured" if usage else "missing"
        return usage if isinstance(usage, dict) else {}, status
    if substrate == _SUBSTRATE_PIPELINE:
        usage = {
            key: payload[key]
            for key in (*_WIRE_USD_KEYS, "credits")
            if key in payload
        }
        return usage, "captured"
    return {}, "missing"


def _wire_member_from_event(
    *,
    signal: str,
    payload: dict[str, Any],
    execution_id: str | None,
) -> dict[str, Any] | None:
    if signal == _SDK_SIGNAL:
        substrate = _SUBSTRATE_SDK
    elif signal == _SNAPSHOT_SIGNAL:
        substrate = _SUBSTRATE_SNAPSHOT
    elif signal == _PIPELINE_SIGNAL:
        substrate = _SUBSTRATE_PIPELINE
    else:
        return None
    usage, status = _member_usage(substrate, payload)
    amount, key = wire_usd_present(usage, status)
    if amount is None:
        return None
    return {
        "substrate": substrate,
        "execution_id": execution_id or payload.get("execution_id"),
        "wire_key": key,
        "wire_usd": amount,
        "usage_capture_status": status,
    }


def _index_wire_members(
    sdk_rows: list[dict[str, Any]],
    snapshot_rows: list[dict[str, Any]],
    pipeline_rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    by_eid: dict[str, list[dict[str, Any]]] = {}
    for signal, rows in (
        (_SDK_SIGNAL, sdk_rows),
        (_SNAPSHOT_SIGNAL, snapshot_rows),
        (_PIPELINE_SIGNAL, pipeline_rows),
    ):
        for row in rows:
            payload = row.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            member = _wire_member_from_event(
                signal=signal,
                payload=payload,
                execution_id=row.get("execution_id"),
            )
            if member is None:
                continue
            eid = member.get("execution_id")
            if not eid:
                continue
            by_eid.setdefault(str(eid), []).append(member)
    return by_eid


def _select_wire(members: list[dict[str, Any]] | None) -> tuple[float | None, str | None]:
    if not members:
        return None, None
    ranked = sorted(
        members,
        key=lambda member: _WIRE_SUBSTRATE_PRIORITY.get(member["substrate"], 0),
        reverse=True,
    )
    for member in ranked:
        amount = member.get("wire_usd")
        if amount is not None:
            return float(amount), str(member.get("wire_key"))
    return None, None


def _rate_x_tokens(row: dict[str, Any], rate: ModelRateRow) -> float | None:
    prompt = row.get("prompt_tokens")
    completion = row.get("completion_tokens")
    if prompt is None and completion is None:
        return None
    total = 0.0
    has_component = False
    if prompt is not None:
        total += (prompt / 1_000_000) * rate.input_rate_per_m
        has_component = True
    if completion is not None:
        total += (completion / 1_000_000) * rate.output_rate_per_m
        has_component = True
    return round(total, 8) if has_component else None


def _price_row(
    row: dict[str, Any],
    wire_members: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    priced = dict(row)
    wire_usd, wire_key = _select_wire(wire_members)
    rate = resolve_rate(row.get("model_id"))

    if wire_usd is not None:
        priced["cost_usd"] = wire_usd
        priced["cost_source"] = "wire"
        priced["wire_key"] = wire_key
    elif rate is not None:
        # All-zero catalog/provider rates are missing prices, not free inference.
        # Intentional local zeros use source=manual_seed_local and may price at $0.
        zero_rate = (
            rate.input_rate_per_m == 0.0 and rate.output_rate_per_m == 0.0
        )
        if zero_rate and rate.source != "manual_seed_local":
            priced["cost_usd"] = None
            priced["cost_source"] = "unavailable"
        else:
            computed = _rate_x_tokens(row, rate)
            if computed is not None:
                priced["cost_usd"] = computed
                priced["cost_source"] = "rate_x_tokens"
            else:
                priced["cost_usd"] = None
                priced["cost_source"] = "unavailable"
        priced["wire_key"] = None
    else:
        priced["cost_usd"] = None
        priced["cost_source"] = "unavailable"
        priced["wire_key"] = None

    if rate is not None:
        priced["input_rate_per_m"] = rate.input_rate_per_m
        priced["output_rate_per_m"] = rate.output_rate_per_m
        priced["rate_source"] = rate.source
    else:
        priced["input_rate_per_m"] = None
        priced["output_rate_per_m"] = None
        priced["rate_source"] = None
    return priced


def _build_pricing_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    wire_count = sum(1 for row in rows if row.get("cost_source") == "wire")
    rate_computed_count = sum(
        1 for row in rows if row.get("cost_source") == "rate_x_tokens"
    )
    unavailable_count = sum(
        1 for row in rows if row.get("cost_source") == "unavailable"
    )
    cdp_stub_count = sum(
        1 for row in rows if row.get("substrate") == "web-anthropic-cdp"
    )
    pricable_rows = [
        row for row in rows if row.get("substrate") != "web-anthropic-cdp"
    ]
    pricable_unavailable = sum(
        1 for row in pricable_rows if row.get("cost_source") == "unavailable"
    )
    total = len(rows)
    pricable_total = len(pricable_rows)
    unavailable_rate_all_rows = (unavailable_count / total) if total else 0.0
    unavailable_rate = (
        (pricable_unavailable / pricable_total) if pricable_total else 0.0
    )
    return {
        "row_count": total,
        "wire_count": wire_count,
        "rate_computed_count": rate_computed_count,
        "unavailable_count": unavailable_count,
        "cdp_stub_count": cdp_stub_count,
        "pricable_row_count": pricable_total,
        "pricable_unavailable_count": pricable_unavailable,
        "unavailable_rate": round(unavailable_rate, 6),
        "unavailable_rate_all_rows": round(unavailable_rate_all_rows, 6),
        "expected_unavailable_fraction_note": (
            "unavailable_rate excludes CDP stubs (token-less by design). "
            "Seed + catalog projection in data/model_rates*.yaml cover charter "
            "seats; rows without rate row or authoritative wire remain unavailable."
        ),
    }


async def dispatch_economics_dollar_equivalents(
    params: dict[str, Any],
    store: EventStore,
) -> dict[str, Any]:
    """Join dollar equivalents atop G2 token rollup without mutating G2 schema."""
    g2_body = await dispatch_economics_token_rollup(params, store)
    since_ts, until_ts, _minutes = await _resolve_window(params, store)
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
    wire_index = _index_wire_members(sdk_rows, snapshot_rows, pipeline_rows)

    priced_rows: list[dict[str, Any]] = []
    for row in g2_body.get("rows") or []:
        eid = row.get("execution_id")
        members = wire_index.get(str(eid)) if eid else None
        priced_rows.append(_price_row(row, members))

    body = dict(g2_body)
    body["rows"] = priced_rows
    body["pricing_audit"] = _build_pricing_audit(priced_rows)
    return body
