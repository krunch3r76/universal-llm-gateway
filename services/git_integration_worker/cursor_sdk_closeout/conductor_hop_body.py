"""Build Stargate team_dispatch body for conductor hops (no summoning-house leak)."""

from __future__ import annotations

import json
from typing import Any

from .conductor_hop_seq import resolve_next_hop_seq_from_row


def build_hop_team_dispatch_body(
    row: dict[str, Any],
    *,
    hop_reason_override: str | None = None,
    ledger_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Clone predecessor ledger record for Stargate ``team_dispatch`` generate."""
    from . import conductor_hop as hop

    if not hop._is_conductor_row(row):
        return None
    record_json = str(row.get("record_json") or "")
    try:
        rec = json.loads(record_json) if record_json else {}
    except json.JSONDecodeError:
        rec = {}
    if not isinstance(rec, dict):
        rec = {}
    if hop_reason_override != "park_harvest" and hop.hop_body_build_refused(row, rec):
        return None
    closeout_tokens = hop._closeout_tokens_from_row(row)
    predecessor_id = str(row.get("dispatch_id") or "")
    thread_id = str(row.get("thread_id") or "")
    source_ref = hop._resolve_source_ref(row, rec)
    if not thread_id or not source_ref:
        return None
    next_seq = resolve_next_hop_seq_from_row(row, rec, ledger_rows=ledger_rows)
    if next_seq is None:
        return None
    hop_reason = hop_reason_override or hop._infer_hop_reason(
        closeout_tokens=closeout_tokens,
        terminal_status=str(row.get("terminal_status") or row.get("status") or ""),
    )
    generation_options = dict(rec.get("generation_options") or {})
    summon_mode = generation_options.get("summon_mode")
    if summon_mode is None and rec.get("summon_mode"):
        summon_mode = rec.get("summon_mode")
    if summon_mode is not None:
        generation_options["summon_mode"] = summon_mode
    generation_options["idempotency_key"] = hop.build_conductor_hop_idempotency_key(
        predecessor_id
    )
    scoreboard_body, scoreboard_sha = hop._scoreboard_text_for_row(row)
    if scoreboard_sha:
        generation_options["scoreboard_tip_sha"] = scoreboard_sha
    if scoreboard_body:
        generation_options["scoreboard_entry_gate"] = hop._scoreboard_entry_gate(
            scoreboard_body
        )
    summoning_thread_id = str(rec.get("summoning_thread_id") or "").strip()
    if summoning_thread_id:
        generation_options["summoning_thread_id"] = summoning_thread_id
    else:
        generation_options["summoning_thread_id_unresolved"] = True
    routing_model = rec.get("model") or row.get("resolved_model")
    raw_contract = str(rec.get("contract") or row.get("contract") or "conductor")
    job = {"none": "freeform", "pure-mechanical": "mechanical"}.get(
        raw_contract, raw_contract
    )
    body: dict[str, Any] = {
        "op": "generate",
        "seat": "cursor-sdk",
        "job": job,
        "lane": rec.get("lane") or "B",
        "caller_agent": "conductor-hop",
        "reuse_thread": thread_id,
        "source_ref": source_ref,
        "model": routing_model,
        "generation_options": generation_options,
    }
    if summoning_thread_id:
        body["dispatch_thread_id"] = summoning_thread_id
    if rec.get("model_knobs"):
        body["model_knobs"] = rec.get("model_knobs")
    wake_lane = row.get("wake_lane") or rec.get("wake_lane")
    if wake_lane:
        body["wake_lane"] = wake_lane
    body["hop_from"] = predecessor_id
    body["hop_seq"] = next_seq
    body["hop_reason"] = hop_reason
    return body
