"""Parse conductor closeout, scoreboard, wait, and harvest recipe for operator view."""

from __future__ import annotations

import json
import re
from typing import Any

from claude_bundles.conductor_stop import (
    is_consult_pending_wait,
    last_next_admit_payload,
    parse_stop_tokens,
)
from implement_admission.conductor_witness_types import row_status_in_tip

_G_ROW_TABLE_RE = re.compile(
    r"^\|\s*(G\d+)\s*\|([^|]+)\|([^|]+)\|",
    re.MULTILINE,
)
_ENTRY_GATE_RE = re.compile(
    r"(?im)^-\s*\*\*NEXT_ADMIT:\*\*\s*(.+)$|^\*\*Entry gate:\*\*\s*(G\d+)",
)
_OPEN_FORK_RE = re.compile(
    r"(?im)^(?:\*\*)?OPEN FORK(?:\*\*)?\s*:?\s*(.+)$",
)


def _json_closeout_fields(body: str) -> dict[str, Any]:
    text = (body or "").strip()
    if not text.startswith("{"):
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    for key in (
        "status",
        "work_outcome",
        "degraded_reason",
        "branch",
        "head_sha",
        "commits_ahead",
        "dispatch_id",
    ):
        if key in data:
            out[key] = data[key]
    usage = data.get("usage")
    if isinstance(usage, dict):
        out["usage"] = usage
    evidence = data.get("evidence_uris")
    if isinstance(evidence, dict):
        paths = evidence.get("artifact_paths")
        if isinstance(paths, list) and paths:
            out["closeout_uri"] = str(paths[0])
        dispatch_ids = evidence.get("dispatch_ids")
        if isinstance(dispatch_ids, list) and dispatch_ids and "dispatch_id" not in out:
            out["dispatch_id"] = str(dispatch_ids[0])
    source_ref = data.get("source_ref")
    if isinstance(source_ref, str) and source_ref.strip():
        out["closeout_uri"] = source_ref.strip()
    return out


def parse_conductor_closeout(
    *,
    body: str,
    dispatch_id: str,
    hop_seq: int | None,
    work_outcome: str | None,
    degraded_reason: str | None,
    closeout_turn: int,
    closeout_uri: str | None,
    usage: dict[str, object] | None,
    branch: str | None,
    head_sha: str | None,
    commits_ahead: int | None,
) -> dict[str, object]:
    """Extract stop_tokens, next_admit, status from conductor closeout prose or JSON."""
    parsed = parse_stop_tokens(body or "")
    json_fields = _json_closeout_fields(body or "")
    stop_tokens = sorted(parsed.tokens)
    next_admit = last_next_admit_payload(body or "") or json_fields.get("next_admit")
    if isinstance(next_admit, str):
        next_admit = next_admit.strip() or None
    status = json_fields.get("status")
    work = work_outcome or json_fields.get("work_outcome")
    degraded = degraded_reason or json_fields.get("degraded_reason")
    dispatch = dispatch_id or str(json_fields.get("dispatch_id") or "")
    usage_out = usage if usage is not None else json_fields.get("usage")
    branch_out = branch or json_fields.get("branch")
    head = head_sha or json_fields.get("head_sha")
    commits = commits_ahead
    if commits is None and json_fields.get("commits_ahead") is not None:
        try:
            commits = int(json_fields["commits_ahead"])
        except (TypeError, ValueError):
            commits = None
    uri = closeout_uri or json_fields.get("closeout_uri")
    return {
        "dispatch_id": dispatch,
        "hop_seq": hop_seq,
        "status": status,
        "work_outcome": work,
        "degraded_reason": degraded,
        "stop_tokens": stop_tokens,
        "next_admit": next_admit,
        "branch": branch_out,
        "head_sha": head,
        "commits_ahead": commits,
        "usage": usage_out or {},
        "closeout_turn": closeout_turn,
        "closeout_uri": uri,
    }


def parse_scoreboard(
    *, body: str, uri: str, sha256: str
) -> dict[str, object]:
    """Rows table + entry_gate + next_admit_tip (last NEXT_ADMIT line in tip)."""
    text = body or ""
    rows: list[dict[str, object]] = []
    for match in _G_ROW_TABLE_RE.finditer(text):
        gid = match.group(1).strip()
        deliverable = match.group(2).strip()
        status_cell = match.group(3).strip()
        mode = ""
        stops = ""
        parts = [p.strip() for p in deliverable.split("|")]
        if len(parts) >= 2:
            deliverable, mode = parts[0], parts[1]
        rows.append(
            {
                "id": gid,
                "deliverable": deliverable,
                "mode": mode,
                "status": status_cell.split()[0] if status_cell else "",
                "stops": stops,
            }
        )
    entry_gate: str | None = None
    for match in _ENTRY_GATE_RE.finditer(text):
        g = match.group(2)
        if g:
            entry_gate = g.strip()
            break
    if entry_gate is None:
        for gid in ("G1", "G2", "G3", "G4", "G5", "G6", "G7"):
            st = row_status_in_tip(text, gid)
            if st and st.upper() not in ("DONE", "CLAIMED"):
                entry_gate = gid
                break
    next_admit_tip = last_next_admit_payload(text)
    return {
        "uri": uri,
        "sha256": sha256,
        "entry_gate": entry_gate,
        "rows": rows,
        "next_admit_tip": next_admit_tip,
    }


def parse_wait_block(
    *,
    closeout_body: str,
    recon_sidecar_body: str | None,
) -> dict[str, object]:
    """open/kind/since/asks[] — asks from OPEN FORK lines when wait open."""
    body = closeout_body or ""
    open_wait = is_consult_pending_wait(body)
    kind = "none"
    if open_wait:
        kind = "CONSULT_PENDING"
    elif "PARKED_TRANSPORT" in parse_stop_tokens(body).tokens:
        kind = "PARKED_TRANSPORT"
    asks: list[str] = []
    sidecar = recon_sidecar_body or ""
    if open_wait and sidecar:
        asks = [m.group(1).strip() for m in _OPEN_FORK_RE.finditer(sidecar)]
    return {
        "open": open_wait,
        "kind": kind,
        "since": None,
        "asks": asks,
        "sidecar_uri": None,
    }


def parse_harvest_recipe(
    *,
    worker_thread_id: str,
    summoning_thread_id: str,
    closeout_turn: int,
    summoning_admit_turn: int,
    subject_template: str = "HARVEST — G{n} …",
    body_template: str = "RULING: …\nNEXT_ADMIT: …",
) -> dict[str, object]:
    """R1/R4: harvest_recipe.threads with after_turn baselines."""
    return {
        "threads": [
            {
                "thread": worker_thread_id,
                "after_turn": closeout_turn,
                "role": "worker",
            },
            {
                "thread": summoning_thread_id,
                "after_turn": summoning_admit_turn,
                "role": "summoning",
            },
        ],
        "subject_template": subject_template,
        "body_template": body_template,
        "send": {
            "thread": worker_thread_id,
            "from_agent": "web-anthropic",
            "to": "cursor",
        },
    }


def compute_next_admit_divergent(
    *,
    closeout_next_admit: str | None,
    scoreboard_next_admit: str | None,
    harvest_next_admit: str | None,
) -> bool:
    """True when latest harvest ≁ scoreboard tip ≁ closeout (normalized compare)."""

    def norm(value: str | None) -> str:
        return " ".join((value or "").lower().split())

    values = [norm(closeout_next_admit), norm(scoreboard_next_admit), norm(harvest_next_admit)]
    values = [v for v in values if v]
    if len(values) < 2:
        return False
    return len(set(values)) > 1
