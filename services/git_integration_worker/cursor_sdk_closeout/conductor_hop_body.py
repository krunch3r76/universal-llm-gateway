"""Build Stargate team_dispatch body for conductor hops (no summoning-house leak)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .conductor_hop_seq import resolve_next_hop_seq_from_row


def _fold_closeout_delta(row: dict[str, Any], rec: dict[str, Any]) -> str | None:
    """Fold a closeout scoreboard delta before admit.

    Returns the post-fold tip sha, ``""`` when the closeout has no delta,
    or None when the fold fails (``auto_fold_failed`` — do not admit).
    """
    delta = rec.get("scoreboard_next_body")
    if not isinstance(delta, str) or not delta.strip():
        return ""
    work_key = str(row.get("work_key") or rec.get("source_ref") or "")
    if not work_key.startswith("todo:"):
        return None
    slug = work_key.split(":", 1)[1].strip()
    if not slug:
        return None
    raw_root = row.get("_files_root")
    files_root = Path(str(raw_root)) if raw_root else None
    from implement_admission.conductor_score_journal import forward_mutate_tip, read_tip

    prior = read_tip(slug, files_root=files_root)
    if prior is None:
        return None
    result = forward_mutate_tip(
        slug,
        next_body=delta,
        seat="conductor-score",
        dispatch_id=str(row.get("dispatch_id") or "") or None,
        reason="auto_fold",
        rows=tuple(),
        delta="closeout scoreboard delta",
        files_root=files_root,
    )
    if result.rejected_reason:
        return None
    after = read_tip(slug, files_root=files_root)
    if after is None:
        return None
    return after[1]


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
    folded_sha = _fold_closeout_delta(row, rec)
    if folded_sha is None:
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
    if folded_sha:
        generation_options["scoreboard_tip_sha"] = folded_sha
        body["generation_options"] = generation_options
    return body
