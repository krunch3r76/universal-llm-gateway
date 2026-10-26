"""Deterministic obligation coverage scoring shared by selector, audit, and finalize."""

from __future__ import annotations

import re
from typing import Any

_TOKEN = re.compile(r"[a-z0-9]{3,}")
_ACTION_CONFIRM = re.compile(
    r"\b(?:applied|application|submit(?:ted|ting)?|completed?|meridial)\b",
    re.I,
)
_DECLINE = re.compile(r"\b(?:decline|unable|cannot|can't|won't|will not)\b", re.I)
_REQUIRED_KINDS = frozenset({"question", "request", "call_to_action"})


def actionable_obligations(obligations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return ledger rows whose obligation field is not ``none``."""
    out: list[dict[str, Any]] = []
    for row in obligations:
        if not isinstance(row, dict):
            continue
        if str(row.get("obligation") or "none") == "none":
            continue
        out.append(row)
    return out


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def _sentences(draft: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", draft.strip())
    return [part.strip() for part in parts if part.strip()]


def _best_sentence(draft: str, needles: set[str]) -> str:
    if not needles:
        return ""
    best = ""
    best_hits = 0
    for sentence in _sentences(draft):
        hits = len(_tokens(sentence) & needles)
        if hits > best_hits:
            best_hits = hits
            best = sentence
    return best


def _reply_mode(row: dict[str, Any], status: str) -> str:
    obligation = str(row.get("obligation") or "none")
    if status == "declined_with_reason":
        return "omit_with_reason"
    if obligation == "act_outside_message":
        return "act_then_confirm"
    if str(row.get("kind") or "") in _REQUIRED_KINDS:
        return "answer_in_message"
    return "answer_in_message"


def verify_obligation(draft: str, row: dict[str, Any]) -> tuple[str, str]:
    """Return ``(status, covering_sentence)`` for one ledger row against ``draft``."""
    quoted = str(row.get("quoted_span") or "")
    obligation = str(row.get("obligation") or "none")
    kind = str(row.get("kind") or "statement")
    if obligation == "none":
        return "addressed", ""
    span_tokens = _tokens(quoted)
    reading_tokens = _tokens(str(row.get("semantic_reading") or ""))
    needles = span_tokens | reading_tokens
    draft_tokens = _tokens(draft)

    if obligation == "act_outside_message":
        for sentence in _sentences(draft):
            if _ACTION_CONFIRM.search(sentence):
                return "addressed", sentence
        if _ACTION_CONFIRM.search(draft):
            return "addressed", _best_sentence(draft, _tokens("applied application submitted"))
        return "missing", ""

    if _DECLINE.search(draft) and needles & draft_tokens:
        sentence = _best_sentence(draft, needles)
        if sentence:
            return "declined_with_reason", sentence
    if re.search(r"\bomit(?:ted|ting)?(?: with reason)?\b", draft, re.I) and needles & draft_tokens:
        sentence = _best_sentence(draft, needles)
        if sentence:
            return "declined_with_reason", sentence

    need = 1
    if kind in _REQUIRED_KINDS:
        need = min(2, max(1, len(span_tokens)))
    overlap = len(span_tokens & draft_tokens) if span_tokens else len(needles & draft_tokens)
    if overlap >= need or (not span_tokens and needles <= draft_tokens):
        return "addressed", _best_sentence(draft, needles) or draft.strip()[:200]

    return "missing", ""


def coverage_map_for_draft(
    draft: str, obligations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build per-obligation coverage rows for ``draft``."""
    rows: list[dict[str, Any]] = []
    for row in obligations:
        status, covering = verify_obligation(draft, row)
        rows.append(
            {
                "id": row.get("id"),
                "kind": row.get("kind"),
                "obligation": row.get("obligation"),
                "reply_mode": _reply_mode(row, status),
                "status": status,
                "covering_sentence": covering,
            }
        )
    return rows


def obligation_coverage_score(draft: str, obligations: list[dict[str, Any]]) -> int:
    """Count actionable obligations marked addressed or declined_with_reason."""
    actionable = actionable_obligations(obligations)
    if not actionable:
        return 0
    cov = coverage_map_for_draft(draft, actionable)
    return sum(
        1
        for row in cov
        if row.get("status") in {"addressed", "declined_with_reason", "omit_with_reason"}
    )


def missing_obligations_block(
    obligations: list[dict[str, Any]], coverage_map: list[dict[str, Any]]
) -> str:
    """Format missing rows for the prose-rhythm reviser prompt."""
    missing_ids = {
        str(row.get("id"))
        for row in coverage_map
        if row.get("status") == "missing" and row.get("id")
    }
    if not missing_ids:
        return ""
    lines: list[str] = []
    by_id = {str(row.get("id")): row for row in obligations if row.get("id")}
    for oid in sorted(missing_ids):
        obl = by_id.get(oid) or {}
        lines.append(
            f"{oid} ({obl.get('kind')} / {obl.get('obligation')}): "
            f"{obl.get('semantic_reading') or obl.get('quoted_span')}"
        )
    body = "\n".join(lines)
    return f"<missing_obligations>\n{body}\n</missing_obligations>"
