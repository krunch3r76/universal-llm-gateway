"""D.1/D.2/D.3/D.4 materializers + D.2 content-hash helper.

Fetches via existing cortex_store.routes.assertions._shared surfaces
(_ASSERTION_COLS, _JSON_FIELDS) and cortex_store.db (cortex_conn, decode_row, query).
No duplication of column lists or decode logic.

Condition dispatch sanitization (migration 060): sub-agent payloads that include
condition entities must pass through ``sanitize_conditions_for_dispatch`` before
being sent. Conditions are redacted per condition_redaction.py; CONFLICT sentinels
cause the dispatch to escalate rather than proceed.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from ..condition_redaction import CONFLICT, apply_condition_to_payload, redact
from ..db import cortex_conn, decode_row, query
from ..routes.assertions._shared import _ASSERTION_COLS, _JSON_FIELDS
from .errors import AgentInjectionAdmissionError, ViolationDetail
from .selection import select
from .templates import render_d1, render_d2, render_d3, render_d4


def compute_d2_content_hash(body_without_hash_line: str) -> str:
    """SHA-256 hex of canonicalized D.2 body with content_hash line OMITTED.
    Canonicalization: rstrip() each line, "\\n".join, encode utf-8, sha256."""
    lines = [line.rstrip() for line in body_without_hash_line.splitlines()]
    canonical = "\n".join(lines).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _entity_exists(conn, entity_id: str) -> bool:
    rows = query(conn, "SELECT 1 FROM entities WHERE id = ?", (entity_id,))
    return bool(rows)


def _fetch_assertion(assertion_id: int) -> dict[str, Any] | None:
    with cortex_conn() as conn:
        rows = query(
            conn,
            f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?",
            (assertion_id,),
        )
    if not rows:
        return None
    return decode_row(rows[0], _JSON_FIELDS)


def materialize_d1(assertion_id: int, *, field_name: str) -> dict[str, Any]:
    """Materialize D.1 STRUCTURED_LOOKUP for a single active assertion."""
    row = _fetch_assertion(assertion_id)
    if row is None:
        raise AgentInjectionAdmissionError(
            f"D.1: assertion {assertion_id} not found",
            violations=[ViolationDetail(invariant=2, detail="not_found")],
        )
    if row.get("superseded_by") is not None:
        raise AgentInjectionAdmissionError(
            f"D.1: assertion {assertion_id} is superseded",
            violations=[ViolationDetail(invariant=2, detail="superseded")],
        )
    if not row.get("claim") or row.get("confidence_score") is None:
        raise AgentInjectionAdmissionError(
            f"D.1: assertion {assertion_id} missing claim or confidence_score",
            violations=[ViolationDetail(invariant=2, detail="missing_fields")],
        )

    utc_now = datetime.utcnow().isoformat()
    ctx = {
        "assertion_id": row["id"],
        "confidence_score": row.get("confidence_score"),
        "valid_from": row.get("valid_from") or "",
        "utc_now": utc_now,
        "field_name": field_name,
        "claim_value": row.get("claim", ""),
    }
    rendered = render_d1(ctx)
    return {
        "kind": "d1",
        "rendered": rendered,
        "assertion_id": row["id"],
        "grade": "structural",
    }


def materialize_d2(
    entity_id: str,
    *,
    selection_strategy: str = "all",
    selection_params: dict | None = None,
    per_entity_limit: int = 200,
    cursor: str | None = None,  # accepted for signature, ignored in 1.0a paging
) -> dict[str, Any]:
    """Materialize D.2 CONTEXT_PROVISION for active assertions on entity.

    Uses two-render + line-strip for content_hash (no str.replace on hash).
    """
    with cortex_conn() as conn:
        if not _entity_exists(conn, entity_id):
            raise AgentInjectionAdmissionError(
                f"D.2: entity {entity_id} not found",
                violations=[ViolationDetail(invariant=2, detail="entity_missing")],
            )
        active_rows = query(
            conn,
            f"SELECT {_ASSERTION_COLS} FROM assertions WHERE entity_id = ? AND superseded_by IS NULL ORDER BY id",
            (entity_id,),
        )
    decoded = [decode_row(r, _JSON_FIELDS) for r in active_rows]
    total_active_count = len(decoded)

    selected = select(decoded, selection_strategy, **(selection_params or {}))

    truncated = False
    out_cursor: str | None = None
    included = selected
    if len(selected) > per_entity_limit:
        if selection_strategy == "all":
            raise AgentInjectionAdmissionError(
                f"D.2: {len(selected)} exceeds per_entity_limit {per_entity_limit} with default strategy",
                violations=[
                    ViolationDetail(invariant=4, detail="overflow_default_strategy")
                ],
            )
        included = selected[:per_entity_limit]
        truncated = True
        out_cursor = f"offset:{per_entity_limit}"

    # build rows_block exactly per spec
    row_lines: list[str] = []
    for a in included:
        rid = a.get("id")
        pred = a.get("predicate_form") or ""
        claim = a.get("claim") or ""
        conf = (
            a.get("confidence_score")
            if a.get("confidence_score") is not None
            else a.get("confidence", "")
        )
        vf = a.get("valid_from") or ""
        row_lines.append(
            f"  assertion_id={rid} predicate={pred} claim={claim} confidence={conf} valid_from={vf}"
        )
    rows_block = "\n".join(row_lines)

    pulled_at = datetime.utcnow().isoformat()
    sel_params_str = "none"
    if selection_params:
        try:
            sel_params_str = json.dumps(
                selection_params, sort_keys=True, separators=(",", ":")
            )
        except Exception:
            sel_params_str = str(selection_params)

    cursor_str = "none" if out_cursor is None else out_cursor
    trunc_str = "true" if truncated else "false"

    # first render with placeholder to get body, then strip hash line, compute, re-render
    ph_ctx: dict[str, Any] = {
        "entity_id": entity_id,
        "included_count": len(included),
        "total_active_count": total_active_count,
        "truncated": trunc_str,
        "selection_strategy": selection_strategy,
        "selection_params": sel_params_str,
        "pulled_at": pulled_at,
        "cursor": cursor_str,
        "content_hash": "<placeholder>",
        "rows_block": rows_block,
    }
    body_with_ph = render_d2(ph_ctx)

    # strip the content_hash line (do not mutate via replace on final hash)
    body_lines = body_with_ph.splitlines()
    body_no_hash_lines = [
        ln for ln in body_lines if not re.match(r"^\s*\| (content_hash|pulled_at):", ln)
    ]
    body_without = "\n".join(body_no_hash_lines)
    if body_with_ph.endswith("\n") and not body_without.endswith("\n"):
        body_without += "\n"

    content_hash = compute_d2_content_hash(body_without)

    final_ctx = dict(ph_ctx)
    final_ctx["content_hash"] = content_hash
    rendered = render_d2(final_ctx)

    return {
        "kind": "d2",
        "rendered": rendered,
        "entity_id": entity_id,
        "included_count": len(included),
        "total_active_count": total_active_count,
        "truncated": truncated,
        "cursor": out_cursor,
        "content_hash": content_hash,
        "grade": "structural",
    }


def materialize_d3(assertion_id: int, *, now: datetime | None = None) -> dict[str, Any]:
    """Materialize D.3 TEMPORAL_QUALIFIED; follows superseded chain to current controller."""
    if now is None:
        now = datetime.utcnow()

    row = _fetch_assertion(assertion_id)
    if row is None:
        raise AgentInjectionAdmissionError(
            f"D.3: assertion {assertion_id} not found",
            violations=[ViolationDetail(invariant=2, detail="not_found")],
        )

    current = row
    superseded_chain_followed = False
    seen: set[int] = set()
    while current.get("superseded_by"):
        sup_id = current.get("superseded_by")
        if sup_id in seen:
            break
        seen.add(sup_id)
        next_row = _fetch_assertion(sup_id)
        if next_row is None:
            break
        current = next_row
        superseded_chain_followed = True

    # compute freshness on the (possibly followed) current
    def _freshness(r: dict[str, Any], n: datetime) -> str:
        vu = r.get("valid_until")
        vf = r.get("valid_from")
        if vu:
            try:
                vu_dt = datetime.fromisoformat(str(vu).replace("Z", "+00:00"))
                if n > vu_dt:
                    return "EXPIRED"
            except Exception:
                pass
        if (not vu or str(vu).strip() == "") and vf:
            try:
                vf_dt = datetime.fromisoformat(str(vf).replace("Z", "+00:00"))
                if (n - vf_dt).days > 365:
                    return "STALE"
            except Exception:
                pass
        return "CURRENT"

    freshness = _freshness(current, now)
    utc_now = now.isoformat()

    ctx = {
        "assertion_id": current["id"],
        "valid_from": current.get("valid_from") or "",
        "valid_until": current.get("valid_until") or "",
        "utc_now": utc_now,
        "freshness": freshness,
        "claim": current.get("claim", ""),
    }
    rendered = render_d3(ctx)

    meta: dict[str, Any] = {"superseded_chain_followed": superseded_chain_followed}
    if superseded_chain_followed:
        meta["original_id"] = assertion_id
        meta["current_id"] = current["id"]

    return {
        "kind": "d3",
        "rendered": rendered,
        "assertion_id": current["id"],
        "freshness": freshness,
        "grade": "structural",
        "_meta": meta,
    }


def materialize_d4(assertion_id: int) -> dict[str, Any]:
    """Materialize D.4 BELIEF_INJECTION — confidence strictly < confirmed + reasoning_summary required."""
    row = _fetch_assertion(assertion_id)
    if row is None:
        raise AgentInjectionAdmissionError(
            f"D.4: assertion {assertion_id} not found",
            violations=[ViolationDetail(invariant=2, detail="not_found")],
        )

    conf = (row.get("confidence") or "").lower()
    if conf == "confirmed" or conf not in {"believed", "suspected", "hypothesized"}:
        raise AgentInjectionAdmissionError(
            f"D.4: confidence {conf} >= confirmed; D.4 is belief-grade only",
            violations=[ViolationDetail(invariant=2, detail="grade_mismatch")],
        )
    if not row.get("reasoning_summary"):
        raise AgentInjectionAdmissionError(
            f"D.4: assertion {assertion_id} missing reasoning_summary",
            violations=[
                ViolationDetail(invariant=2, detail="missing_reasoning_summary")
            ],
        )

    ctx = {
        "assertion_id": row["id"],
        "confidence_score": row.get("confidence_score"),
        "derivation_type": row.get("derivation_type") or "",
        "seeded_by": row.get("seeded_by") or "",
        "seeded_at": row.get("created_at") or "",
        "claim": row.get("claim", ""),
        "reasoning_summary": row.get("reasoning_summary") or "",
    }
    rendered = render_d4(ctx)
    return {
        "kind": "d4",
        "rendered": rendered,
        "assertion_id": row["id"],
        "grade": "belief",
    }


def sanitize_conditions_for_dispatch(
    payload: dict[str, Any],
    *,
    audience: str = "sub_agent",
    surface: str = "dispatch",
) -> dict[str, Any]:
    """Redact condition entities in *payload* for sub-agent dispatch.

    Iterates over any ``conditions`` list in the payload and applies
    condition_redaction per condition's reveal_default + safety_invariant.

    CONFLICT return from the redactor causes the payload to carry a
    ``dispatch_blocked_by_conflict`` flag — callers MUST NOT send the payload
    to the sub-agent; they must escalate to the orchestrator/lead.

    Returns a new dict (does not mutate *payload*).
    """
    out = dict(payload)
    raw_conditions = out.pop("conditions", None)
    if not raw_conditions or not isinstance(raw_conditions, list):
        return out

    aud: Any = audience if audience in ("orchestrator_lead", "sub_agent", "log_sink") else "sub_agent"
    sanitized: list[dict[str, Any]] = []
    has_conflict = False

    for cond_attrs in raw_conditions:
        if not isinstance(cond_attrs, dict):
            continue
        reveal_default = str(cond_attrs.get("reveal_default", "open"))
        sv = cond_attrs.get("surface_visibility")
        sv_map = sv if isinstance(sv, dict) else None
        safety_invariant = bool(cond_attrs.get("safety_invariant", False))

        level = redact(
            reveal_default=reveal_default,
            surface_visibility=sv_map,
            safety_invariant=safety_invariant,
            surface=surface,
            audience=aud,
        )
        if level == CONFLICT:
            has_conflict = True
            out["dispatch_blocked_by_conflict"] = {
                "reason": "CONFLICT: safety-invariant condition cannot be safely hidden for this dispatch. Escalate to orchestrator/lead.",
                "condition_id": cond_attrs.get("entity_id") or cond_attrs.get("id"),
            }
            break
        if level == "hidden":
            continue
        if level == "sanitized":
            narrative = str(cond_attrs.get("narrative", ""))
            sanitized.append(
                {
                    "lifecycle": cond_attrs.get("lifecycle"),
                    "reveal_default": reveal_default,
                    "safety_invariant": safety_invariant,
                    "narrative_head": narrative.split("\n")[0][:200] if narrative else "",
                    "redaction_level": "sanitized",
                }
            )
        else:  # full — only reached for orchestrator_lead
            sanitized.append(dict(cond_attrs))

    if not has_conflict and sanitized:
        out["conditions"] = sanitized
    return out
