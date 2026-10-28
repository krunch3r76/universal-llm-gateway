"""Finalize helpers: usage, cost, omit-with-reason, obligation gate."""

from __future__ import annotations

import json
import re
from typing import Any

from systems.pipeline.core.handlers.protocol import StepOutput

from .email_envelope import polish_email_draft

_REQUIRED_ASK_KINDS = frozenset({"question", "request", "call_to_action"})

_GENERATE_STEPS = (
    ("source_obligations", "prompt_architect"),
    ("prompt_architect", "prompt_architect"),
    ("drafter_openai", "drafter_openai"),
    ("drafter_google", "drafter_google"),
    ("drafter_xai", "drafter_xai"),
    ("drafter_local_hermes", "drafter_local_hermes"),
    ("drafter_local_ernie", "drafter_local_ernie"),
    ("drafter_anthropic", "drafter_anthropic"),
    ("obligation_coverage", "reviewer"),
    ("fact_ceiling_audit", "verifier"),
    ("prose_rhythm_pass", "reviser"),
)

def _step(payload: dict[str, Any]) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload)


def _attach_status(payload: dict[str, Any]) -> dict[str, Any]:
    """Map refused/degraded runs to honest top-level status."""
    if payload.get("refused"):
        payload["status"] = "refused"
        if not payload.get("error"):
            payload["error"] = str(payload.get("refused"))
        return payload
    ship = payload.get("ship_gate") or {}
    review = payload.get("review") or {}
    degraded_reasons: list[str] = []
    if ship.get("pass") is False:
        unresolved = ship.get("unresolved") or []
        if unresolved and isinstance(unresolved[0], dict):
            degraded_reasons.append(
                str(unresolved[0].get("detail") or "ship_gate_unresolved")
            )
        else:
            degraded_reasons.append("ship_gate_failed")
    if review.get("status") == "failed":
        degraded_reasons.append(str(review.get("reason") or "review_failed"))
    if degraded_reasons:
        payload["status"] = "degraded"
        payload["error"] = "; ".join(degraded_reasons)
    elif "status" not in payload:
        payload["status"] = "complete"
    return payload


def _raw_step(outputs: dict[str, Any], name: str) -> Any:
    return outputs.get(name)


def _step_ok(step: Any) -> bool:
    if step is None:
        return False
    if isinstance(step, dict):
        return not step.get("error")
    return not getattr(step, "error", None)


_NUMBERED_PARAGRAPH = re.compile(r"(?m)^\d+\.\s")
_NUMBERED_LIST_BODY = re.compile(r"(?m)^\s*(?:\d+\.\s+|[-*•]\s+)\S")
_BRACKET_PLACEHOLDER = re.compile(r"\[\s*\d+\s*/\s*\d+\s*\]")

_V2_DISCARDED_VIOLATION_TYPES = frozenset(
    {
        "v2_dropped_name",
        "v2_dropped_number",
        "fact_reordered",
    }
)


def _drift_failed(row: Any) -> bool:
    return isinstance(row, dict) and row.get("passed") is False


def _quality_violation_applies_to_body(body: str, row: dict[str, Any]) -> bool:
    """Drop quality rows that allege structure absent from the selected draft body."""
    if str(row.get("type") or "") != "quality_violation":
        return True
    blob = f"{row.get('claim_id') or ''} {row.get('detail') or ''}".lower()
    if "numbered" in blob or "bullet" in blob:
        return _NUMBERED_LIST_BODY.search(body) is not None
    if "placeholder" in blob or "bracket" in blob or "[3" in blob:
        return _BRACKET_PLACEHOLDER.search(body) is not None
    return True


def _ship_gate_violation_for_selected(
    row: dict[str, Any], *, selected: str, rhythm_reverted: bool
) -> bool:
    """True when a provenance row should block ship for the selected draft."""
    del rhythm_reverted
    if selected != "draft_v1":
        return True
    vtype = str(row.get("type") or "")
    if vtype in _V2_DISCARDED_VIOLATION_TYPES:
        return False
    return True


def filter_ship_gate_unresolved(
    rows: list[Any],
    *,
    selected_body: str,
    selected: str,
    rhythm_reverted: bool,
) -> list[Any]:
    """Keep only violations attributable to ``selected_body`` for the ship gate."""
    kept: list[Any] = []
    for row in rows:
        if not isinstance(row, dict):
            kept.append(row)
            continue
        if not _ship_gate_violation_for_selected(
            row, selected=selected, rhythm_reverted=rhythm_reverted
        ):
            continue
        if not _quality_violation_applies_to_body(selected_body, row):
            continue
        kept.append(row)
    return kept


def _merge_drift(v1: dict[str, Any], v2: dict[str, Any] | None) -> dict[str, Any]:
    """Envelope drift fails when the v1 check or the v2 check fails.

    Flag rows from both passes are kept. A missing v2 check does not fail
    the envelope. Callers use the merged ``passed`` bit for the ship gate
    so a clean v1 cannot hide a drifted rhythm draft.
    """
    flags = list(v1.get("flagged_sentences") or [])
    if isinstance(v2, dict):
        flags.extend(list(v2.get("flagged_sentences") or []))
    return {
        **v1,
        "passed": not _drift_failed(v1) and not _drift_failed(v2),
        "flagged_sentences": flags,
    }


def _polish_email_draft_if_needed(assemble: dict[str, Any], text: str) -> str:
    if str(assemble.get("channel") or "") != "email" or not text.strip():
        return text
    intake = assemble.get("intake")
    intake_dict = intake if isinstance(intake, dict) else {}
    recipient = str(intake_dict.get("recipient") or assemble.get("recipient") or "")
    signer = str(intake_dict.get("signer") or "")
    return polish_email_draft(
        text,
        recipient=recipient,
        signer=signer,
        intake=intake_dict,
        pin_ledger=list(assemble.get("pin_ledger") or []),
    )


def _selected_body(selected: str, draft: Any, revision: dict[str, Any] | None) -> str:
    if selected == "draft_v2" and isinstance(revision, dict):
        text = revision.get("draft_v2")
        if isinstance(text, str):
            return text
    if isinstance(draft, dict):
        inner = draft.get("draft")
        if isinstance(inner, str):
            return inner
    return ""


def _stitch_filing(frame: dict[str, Any], body: str) -> str:
    """Join source caption, opener, and jurat around the model body.

    Only non-empty source slices are copied. A missing caption or jurat is
    left out rather than replaced with a stock line. The body is the model
    text already checked for fact tokens.
    """
    parts: list[str] = []
    for key in ("caption", "opener"):
        piece = frame.get(key)
        if isinstance(piece, str) and piece.strip():
            parts.append(piece.strip("\n"))
    if body.strip():
        parts.append(body.strip("\n"))
    jurat = frame.get("jurat")
    if isinstance(jurat, str) and jurat.strip():
        parts.append(jurat.strip("\n"))
    return "\n\n".join(parts)


def _json_of(outputs: dict[str, Any], name: str) -> dict[str, Any] | None:
    step = _raw_step(outputs, name)
    if not _step_ok(step):
        return None
    data = step.get("json") if isinstance(step, dict) else getattr(step, "json", None)
    if isinstance(data, dict) and data.get("_skipped") is True:
        return None
    return data if isinstance(data, dict) else None


def _usage_of(step: Any) -> dict[str, int] | None:
    if not _step_ok(step):
        return None
    prompt = (
        getattr(step, "prompt_tokens", None)
        if not isinstance(step, dict)
        else step.get("prompt_tokens")
    )
    completion = (
        getattr(step, "completion_tokens", None)
        if not isinstance(step, dict)
        else step.get("completion_tokens")
    )
    calls = (
        getattr(step, "model_call_count", 0)
        if not isinstance(step, dict)
        else step.get("model_call_count", 0)
    ) or 0
    if prompt is None and completion is None and calls == 0:
        return None
    return {
        "prompt_tokens": int(prompt or 0),
        "completion_tokens": int(completion or 0),
    }


def _model_of(step: Any) -> str | None:
    if step is None:
        return None
    if isinstance(step, dict):
        model = step.get("model_id")
    else:
        model = getattr(step, "model_id", None)
    return str(model) if model else None


def _rates() -> tuple[dict[str, tuple[float, float]], dict[str, str]]:
    from event_store.model_rate_table import load_manual_rows

    rows, aliases = load_manual_rows()
    table = {
        model_id: (row.input_rate_per_m, row.output_rate_per_m)
        for model_id, row in rows.items()
    }
    return table, aliases


def _violation_key(row: Any) -> tuple[tuple[str, str], ...] | None:
    if not isinstance(row, dict):
        return None
    return tuple(sorted((str(key), str(value)) for key, value in row.items()))


def _v2_worse(initial: list[Any], final: list[Any]) -> bool:
    """True only when final has more violations than initial.

    A longer list returns immediately, so the keyed branch runs only when
    the lists are the same length or final is shorter. Equal or fewer
    violations are not worse. A different set of the same size is not worse.
    Gate evaluation then stays on v2.
    """
    if len(final) > len(initial):
        return True
    initial_keys = [key for row in initial if (key := _violation_key(row))]
    final_keys = [key for row in final if (key := _violation_key(row))]
    bag = list(final_keys)
    contained = True
    for key in initial_keys:
        if key not in bag:
            contained = False
            break
        bag.remove(key)
    return contained and len(final_keys) > len(initial_keys)


_OMIT_WITH_REASON = re.compile(
    r"\b(O\d+)\s*(?:\([^)]*\))?\s*:\s*omit_with_reason\b",
    re.I,
)
_OMIT_WITH_REASON_PROXIMITY = re.compile(
    r"\b(O\d+)\b(?:(?!\bO\d+\b)[^\n]){0,80}\bomit_with_reason\b",
    re.I,
)


def _omit_with_reason_ids(architect_brief: str) -> set[str]:
    ids = {match.group(1).upper() for match in _OMIT_WITH_REASON.finditer(architect_brief)}
    ids |= {
        match.group(1).upper()
        for match in _OMIT_WITH_REASON_PROXIMITY.finditer(architect_brief)
    }
    return ids


def _apply_architect_omit_with_reason(
    coverage_map: list[dict[str, Any]],
    architect_brief: str,
    *,
    channel: str,
) -> list[dict[str, Any]]:
    """Mark architect-declared omissions as covered (declined_with_reason)."""
    omit_ids = _omit_with_reason_ids(architect_brief)
    if not omit_ids:
        return coverage_map
    updated: list[dict[str, Any]] = []
    for row in coverage_map:
        oid = str(row.get("id") or "").upper()
        if oid in omit_ids:
            updated.append(
                {
                    **row,
                    "status": "declined_with_reason",
                    "reply_mode": "omit_with_reason",
                    "covering_sentence": row.get("covering_sentence")
                    or "omit_with_reason per architect brief",
                }
            )
        else:
            updated.append(dict(row))
    return updated


from .finalize_payload import (  # noqa: E402, F401
    _cost,
    _obligation_coverage_gate,
    build_finalize_payload,
)

