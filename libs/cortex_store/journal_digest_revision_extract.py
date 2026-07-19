"""Revision-mode EXTRACT — keep / revise / remove decisions for re-talk deltas."""

from __future__ import annotations

import json
from typing import Any

from universal_logging import get_logger

from .journal_digest_extract import (
    _chat_completion,
    strip_json_fences,
    validate_claim,
)

logger = get_logger("cortex-api.journal_digest_revision_extract")

_REVISION_SYSTEM = """\
You revise a digested journal section after operator re-talk.

For each PRIOR assertion (by id), choose exactly one decision:
- keep: still supported by revised text
- revise: operator corrected/refined this claim — emit successor claim fields
- remove: operator disavowed with no replacement

Also emit fresh "add" claims for new facts in the re-talk absent from priors.

Map decisions to Mem0 ops: add, update (revise), delete (remove), no-op (keep).

Return JSON:
{
  "decisions": [
    {"prior_id": 123, "decision": "keep|revise|remove", "verbatim_evidence": "..."},
    ...
  ],
  "adds": [ {claim, p_class, canonicality, attach_hint, flags, evidence_anchor}, ... ],
  "flags": [ {"prior_id": 456, "flag": "premise_shift", "note": "..."}, ... ]
}

Rules:
- revise/remove rows MUST quote operator verbatim correcting words in verbatim_evidence
- keep predecessor class in history; class successors fresh from correcting words
- P2 disputes ≠ corrections — do not remove counterparty statements unless mis-capture
- Return ONLY valid JSON. No markdown fences."""


def _format_prior_block(priors: list[dict[str, Any]]) -> str:
    lines = ["Prior assertions:"]
    for row in priors:
        lines.append(
            f"- id={row['id']} entity={row.get('entity_id')} "
            f"claim={row.get('claim')!r} derivation={row.get('derivation_type')}"
        )
    return "\n".join(lines)


def parse_revision_batch(
    raw_text: str,
    *,
    entry_anchor: str,
    journal_uri: str,
    prior_assertions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Parse revision JSON into a normalized envelope."""
    cleaned = strip_json_fences(raw_text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Revision extract invalid JSON: %.200s", cleaned)
        return None
    if not isinstance(parsed, dict):
        return None

    decisions_raw = parsed.get("decisions")
    if not isinstance(decisions_raw, list):
        return None

    prior_ids = {int(p["id"]) for p in prior_assertions}
    decisions: list[dict[str, Any]] = []
    for item in decisions_raw:
        if not isinstance(item, dict):
            continue
        prior_id = item.get("prior_id")
        decision = item.get("decision")
        if prior_id is None or decision not in {"keep", "revise", "remove"}:
            continue
        pid = int(prior_id)
        if pid not in prior_ids:
            continue
        decisions.append(
            {
                "prior_id": pid,
                "decision": decision,
                "verbatim_evidence": str(item.get("verbatim_evidence") or ""),
                "successor": item.get("successor"),
            }
        )

    adds: list[dict[str, Any]] = []
    for item in parsed.get("adds") or []:
        validated = validate_claim(item)
        if validated is not None:
            adds.append(validated)

    flags: list[dict[str, Any]] = []
    for item in parsed.get("flags") or []:
        if isinstance(item, dict) and item.get("prior_id") is not None:
            flags.append(item)

    return {
        "entry_anchor": entry_anchor,
        "journal_uri": journal_uri,
        "decisions": decisions,
        "adds": adds,
        "flags": flags,
        "prior_assertions": prior_assertions,
    }


def extract_revision_decisions(
    entry_text: str,
    *,
    entry_anchor: str,
    journal_uri: str,
    prior_assertions: list[dict[str, Any]],
    prior_text: str | None = None,
) -> dict[str, Any] | None:
    """LLM revision pass or None on failure."""
    if not prior_assertions:
        return None

    diff_block = ""
    if prior_text and prior_text.strip() != entry_text.strip():
        diff_block = (
            f"\nPrior section text:\n{prior_text}\n\nRevised section text:\n{entry_text}"
        )
    else:
        diff_block = f"\nRevised section text:\n{entry_text}"

    user_prompt = (
        f"Journal URI: {journal_uri}\nEntry anchor: {entry_anchor}\n"
        f"{_format_prior_block(prior_assertions)}{diff_block}"
    )
    result = _chat_completion(_REVISION_SYSTEM, user_prompt)
    if result is None:
        return None
    return parse_revision_batch(
        result,
        entry_anchor=entry_anchor,
        journal_uri=journal_uri,
        prior_assertions=prior_assertions,
    )


__all__ = ["extract_revision_decisions", "parse_revision_batch"]
