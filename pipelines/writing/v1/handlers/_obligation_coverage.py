"""Deterministic obligation coverage scoring shared by selector, audit, and finalize."""

from __future__ import annotations

import re
from typing import Any

_TOKEN = re.compile(r"[a-z0-9]{3,}")
_LINK_TARGET = r"(?:the\s+)?(?:link|portal|form|site|url|page|https?://\S+)"
_FP_ACTION = (
    r"\b(?:i(?:'ve|\s+have)?\s+(?:applied|submitted|completed|registered|booked|"
    r"scheduled|uploaded|signed\s+up)|i(?:'ll|\s+will|\s+plan\s+to|\s+am\s+going\s+to)"
    r"\s+(?:apply|submit|complete|register|book|schedule|upload|sign\s+up))\s+"
    r"(?:for\b|to\b|on\b|in\b|at\b|via\b|through\b|online\b|(?:my|the|an|this|that)\s+"
    r"(?:application|form|profile|resume|cv|link|portal|slot|time|call|interview|role|"
    r"position|registration))\b"
)
_ACTION_CONFIRM = re.compile(
    rf"(?:"
    rf"{_FP_ACTION}|"
    rf"\bsubmitting\s+my\s+application\b|"
    rf"\b(?:applied|apply|submitted|submit|registered|register|signed\s+up|sign\s+up|"
    rf"booked|book|scheduled|schedule)\s+(?:through|via|at|on)\s+{_LINK_TARGET}\b"
    rf")",
    re.I,
)
_CTA_DECLINE_STEM = re.compile(
    r"\b(?:appl(?:y|ied|ication|ying)|submit(?:ted|ting)?|"
    r"regist(?:er|ered|ering|ration)|book(?:ed|ing)?|schedul(?:e|ed|ing)|"
    r"upload(?:ed|ing)?|sign(?:ed|ing)?\s+up|complet(?:e|ed|ing)|link|portal|form|"
    r"role|position)\b",
    re.I,
)
_DECLINE = re.compile(
    r"\b(?:i\s+(?:decline|cannot|can't|won't|will\s+not|am\s+unable)|unable\s+to)\b",
    re.I,
)
_ENVELOPE_LINE = re.compile(
    r"^(?:to|from|cc|bcc|date|subject|re)\s*:",
    re.I,
)
_VALEDiction = re.compile(
    r"^(?:best|sincerely|thanks|thank you|regards|cheers),?\s*\.?\s*$",
    re.I,
)
_STOPWORDS = frozenset(
    {
        "can",
        "you",
        "your",
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "are",
        "was",
        "were",
        "have",
        "has",
        "had",
        "will",
        "would",
        "could",
        "should",
        "please",
        "what",
        "when",
        "where",
        "which",
        "who",
        "how",
        "answer",
        "question",
        "respond",
        "request",
        "acknowledge",
        "reflect",
        "statement",
        "deadline",
        "about",
        "into",
        "their",
        "they",
        "them",
        "our",
        "out",
    }
)
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


def _normalize_apostrophes(text: str) -> str:
    return text.replace("\u2019", "'").replace("\u2018", "'")


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN.findall(text.lower())
        if token not in _STOPWORDS
    }


def _is_envelope_or_closing(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if _ENVELOPE_LINE.match(stripped):
        return True
    if _VALEDiction.match(stripped):
        return True
    if re.search(r"[.!?]\s*$", stripped):
        return False
    if (
        re.match(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s*$", stripped)
        and len(stripped) < 40
    ):
        return True
    return False


def _covering_in_body(covering: str, body: str) -> bool:
    text = covering.strip()
    if not text:
        return False
    return text in _sentences(body)


def apply_audit_overlay(
    base: dict[str, Any],
    audit: dict[str, Any] | None,
    body: str,
) -> dict[str, Any]:
    """Merge an audit/model row onto deterministic coverage with body validation."""
    if not audit:
        return dict(base)
    obligation = str(base.get("obligation") or audit.get("obligation") or "")
    det_status = str(base.get("status") or "missing")
    status = str(audit.get("status") or det_status)
    covering = str(audit.get("covering_sentence") or base.get("covering_sentence") or "")

    if status == "missing":
        row = {**base, **audit, "status": "missing", "covering_sentence": covering}
        row["reply_mode"] = _reply_mode(base, "missing")
        return row

    if status not in {"addressed", "declined_with_reason"}:
        return dict(base)

    if not _covering_in_body(covering, body):
        return dict(base)

    if status == "declined_with_reason" and det_status == "missing":
        if not _sentence_declines(covering):
            return dict(base)
        quoted_tokens = _tokens(str(base.get("quoted_span") or ""))
        covering_norm = _normalize_apostrophes(covering)
        if obligation == "act_outside_message":
            if not (
                _CTA_DECLINE_STEM.search(covering_norm)
                or (quoted_tokens and quoted_tokens & _tokens(covering))
            ):
                return dict(base)
        elif not (
            quoted_tokens and quoted_tokens & _tokens(covering)
        ):
            return dict(base)

    if obligation == "act_outside_message" and status == "addressed":
        if not _ACTION_CONFIRM.search(_normalize_apostrophes(covering)):
            return dict(base)

    row = {**base, **audit, "status": status, "covering_sentence": covering}
    row["reply_mode"] = _reply_mode(base, status)
    if status == "declined_with_reason":
        row["reply_mode"] = str(audit.get("reply_mode") or "omit_with_reason")
    return row


def _sentences(draft: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", draft.strip())
    body: list[str] = []
    for part in parts:
        text = part.strip()
        if not text or _is_envelope_or_closing(text):
            continue
        body.append(text)
    return body


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


def _sentence_declines(sentence: str) -> bool:
    norm = _normalize_apostrophes(sentence)
    if re.search(r"\b(?:can't|cannot)\s+wait\b", norm, re.I):
        return False
    return _DECLINE.search(norm) is not None


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
    body_sentences = _sentences(draft)

    if obligation == "act_outside_message":
        for sentence in body_sentences:
            if _ACTION_CONFIRM.search(_normalize_apostrophes(sentence)):
                return "addressed", sentence
        return "missing", ""

    for sentence in body_sentences:
        sent_tokens = _tokens(sentence)
        if not (span_tokens & sent_tokens):
            continue
        if _sentence_declines(sentence):
            return "declined_with_reason", sentence

    if re.search(r"\bomit(?:ted|ting)?(?: with reason)?\b", draft, re.I):
        sentence = _best_sentence(draft, span_tokens)
        if sentence and span_tokens & _tokens(sentence):
            return "declined_with_reason", sentence

    need = 1
    if kind in _REQUIRED_KINDS:
        need = min(2, max(1, len(span_tokens)))
    draft_tokens = _tokens("\n".join(body_sentences))
    overlap = len(span_tokens & draft_tokens)
    if overlap >= need:
        return "addressed", _best_sentence(draft, span_tokens)

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
                "quoted_span": str(row.get("quoted_span") or ""),
                "reply_mode": _reply_mode(row, status),
                "status": status,
                "covering_sentence": covering,
            }
        )
    return rows


def merge_audit_coverage_map(
    selected_body: str,
    obligations: list[dict[str, Any]],
    audit_map: list[dict[str, Any]] | None,
    *,
    selected: str,
) -> list[dict[str, Any]]:
    """Blend obligation_coverage audit rows with a fresh deterministic pass."""
    del selected
    actionable = actionable_obligations(obligations)
    fresh = coverage_map_for_draft(selected_body, actionable)
    if not audit_map:
        return fresh
    audit_by_id = {str(row.get("id")): row for row in audit_map if row.get("id")}
    return [
        apply_audit_overlay(row, audit_by_id.get(str(row.get("id") or "")), selected_body)
        for row in fresh
    ]


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
