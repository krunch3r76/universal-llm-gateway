"""Deterministic obligation coverage scoring shared by selector, audit, and finalize."""

from __future__ import annotations

import re
from typing import Any

_TOKEN = re.compile(r"[a-z0-9]{3,}")
_LINK_TARGET = r"(?:the\s+)?(?:link|portal|form|site|url|page|https?://\S+)"
_FP_PREP = r"(?:for|to|on|in|at|via|through)"
_FP_TARGET = (
    r"(?:(?:(?:the|this|that|your|my|an?)\s+)?"
    r"(?:link|portal|form|url|site|page|call|interview|calendar)\b|"
    r"(?:a|this|that|your|my)\s+(?:time|slot)\b|"
    r"(?:the|this|that|your|my)\s+(?:role|position)(?!\s+(?:of|at)\b)\b|"
    r"(?:the|this|that|your|my)\s+(?:application|opening|posting)\b|"
    r"https?://\S+)"
)
_FP_APP_MIDDLE = r"(?:(?:my|the|this|that|your)\s+application\s+)?"
_PAST_FP_ACTION = (
    r"\b(?:i(?:'ve|\s+have)?\s+(?:applied|submitted|completed|registered|booked|"
    r"scheduled|uploaded|signed\s+up))"
    rf"(?:\s+online\b|\s+{_FP_APP_MIDDLE}(?:{_FP_PREP}\s+{_FP_TARGET}))"
)
_PAST_LINK_CONFIRM = (
    rf"\b(?:applied|submitted|completed|registered|signed\s+up|booked|scheduled)"
    rf"\s+(?:through|via|at|on)\s+{_LINK_TARGET}\b"
)
_PAST_ACTION_CONFIRM = re.compile(
    rf"(?:{_PAST_FP_ACTION}|{_PAST_LINK_CONFIRM})",
    re.I,
)
_INTENT_FP_ACTION = (
    r"\b(?:i(?:'ll|\s+will|\s+plan\s+to|\s+am\s+going\s+to)\s+(?:apply|submit|"
    r"complete|register|book|schedule|upload|sign\s+up))"
    rf"(?:\s+online\b|\s+{_FP_APP_MIDDLE}(?:{_FP_PREP}\s+{_FP_TARGET}))"
)
_INTENT_OR_ASK_BACK_CONFIRM = re.compile(
    rf"(?:"
    rf"{_INTENT_FP_ACTION}|"
    rf"\bsubmitting\s+my\s+application\b|"
    rf"\b(?:apply|submit)(?:ing)?\s+(?:through|via|at|on)\s+{_LINK_TARGET}\b"
    rf")",
    re.I,
)
_ACTION_CONFIRM = re.compile(
    rf"(?:{_PAST_FP_ACTION}|{_INTENT_FP_ACTION}|{_PAST_LINK_CONFIRM}|"
    rf"\bsubmitting\s+my\s+application\b|"
    rf"\b(?:apply|submit)(?:ing)?\s+(?:through|via|at|on)\s+{_LINK_TARGET}\b)",
    re.I,
)
_CTA_DECLINE_STEM = re.compile(
    r"\b(?:appl(?:y|ied|ication|ying)|submit(?:ted|ting)?|"
    r"regist(?:er|ered|ering|ration)|book(?:ed|ing)?|schedul(?:e|ed|ing)|"
    r"upload(?:ed|ing)?|sign(?:ed|ing)?\s+up)\b",
    re.I,
)
_DECLINE = re.compile(
    r"\b(?:i\s+(?:decline|cannot|can't|won't|will\s+not|am\s+unable)"
    r"|(?:can't|cannot)\s+(?:take|cover)"
    r"|won't\s+be\s+able\s+to\s+(?:take|cover)"
    r"|unable\s+to\s+(?:take|cover))\b",
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


_PAST_ACTION_NEGATION = re.compile(
    r"\b(?:not|never|no\s+need\s+to|haven't|hasn't|hadn't|didn't|did\s+not|"
    r"have\s+not|has\s+not|had\s+not|yet\s+to|without)\b",
    re.I,
)
_INTENT_ACTION_NEGATION = re.compile(
    r"\b(?:not|never|no\s+need\s+to|won't|will\s+not|don't|do\s+not|"
    r"cannot|can't|unable\s+to|without)\b",
    re.I,
)
_PAST_ACTION_TRAILING_NEGATION = re.compile(
    r"\b(?:but\s+i\s+(?:haven't|have\s+not|didn't|did\s+not|never))\b",
    re.I,
)


def _match_is_negated(
    norm: str, match: re.Match[str], negation: re.Pattern[str]
) -> bool:
    start = match.start()
    prefix = norm[max(0, start - 30) : start]
    if negation.search(prefix):
        return True
    return negation.search(match.group(0)) is not None


def _has_affirmative_past_action(text: str) -> bool:
    """Past-action on operator pin excerpts; negation windows fail safe."""
    norm = _normalize_apostrophes(text)
    for match in _PAST_ACTION_CONFIRM.finditer(norm):
        if _match_is_negated(norm, match, _PAST_ACTION_NEGATION):
            continue
        trailing = norm[match.end() : match.end() + 25]
        if _PAST_ACTION_TRAILING_NEGATION.search(trailing):
            continue
        return True
    return False


def _draft_sentence_has_past_action_claim(sentence: str) -> bool:
    """Raw past-action regex on draft text; no negation window (fail closed)."""
    return (
        _PAST_ACTION_CONFIRM.search(_normalize_apostrophes(sentence)) is not None
    )


def _has_affirmative_intent_action(text: str) -> bool:
    norm = _normalize_apostrophes(text)
    for match in _INTENT_OR_ASK_BACK_CONFIRM.finditer(norm):
        if not _match_is_negated(norm, match, _INTENT_ACTION_NEGATION):
            return True
    return False


def has_attested_external_act(pin_ledger: list[dict[str, Any]] | None) -> bool:
    """True when an attested pin excerpt records a completed external action."""
    if not pin_ledger:
        return False
    for pin in pin_ledger:
        if not isinstance(pin, dict):
            continue
        if pin.get("attested") is not True:
            continue
        provenance = pin.get("provenance")
        if provenance not in {"operator_account", "document", None}:
            continue
        excerpt = str(pin.get("excerpt") or "")
        if _has_affirmative_past_action(excerpt):
            return True
    return False


def _effective_act_attested(pin_ledger: list[dict[str, Any]] | None) -> bool:
    """Legacy calls omit pin_ledger; pipeline callers pass assemble ledger."""
    if pin_ledger is None:
        return True
    return has_attested_external_act(pin_ledger)


def _act_outside_sentence_covers(sentence: str, *, act_attested: bool) -> bool:
    if not act_attested and _draft_sentence_has_past_action_claim(sentence):
        return False
    if act_attested and _draft_sentence_has_past_action_claim(sentence):
        return True
    if _has_affirmative_intent_action(sentence):
        return True
    return False


def _body_has_unattested_past_claim(body: str, *, act_attested: bool) -> bool:
    if act_attested:
        return False
    return any(_draft_sentence_has_past_action_claim(s) for s in _sentences(body))


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
    *,
    pin_ledger: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge an audit/model row onto deterministic coverage with body validation."""
    act_attested = _effective_act_attested(pin_ledger)
    if not audit:
        return dict(base)
    obligation = str(base.get("obligation") or audit.get("obligation") or "")
    det_status = str(base.get("status") or "missing")
    status = str(audit.get("status") or det_status)
    covering = str(audit.get("covering_sentence") or base.get("covering_sentence") or "")

    if status == "missing":
        row = {**base, **audit, "status": "missing", "covering_sentence": covering}
        row["reply_mode"] = _reply_mode(base, "missing", act_attested=act_attested)
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

    if obligation == "act_outside_message":
        if _body_has_unattested_past_claim(body, act_attested=act_attested):
            return dict(base)
        if status == "addressed" and not _act_outside_sentence_covers(
            covering, act_attested=act_attested
        ):
            return dict(base)

    row = {**base, **audit, "status": status, "covering_sentence": covering}
    row["reply_mode"] = _reply_mode(base, status, act_attested=act_attested)
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


def _reply_mode(row: dict[str, Any], status: str, *, act_attested: bool = False) -> str:
    obligation = str(row.get("obligation") or "none")
    if status == "declined_with_reason":
        return "omit_with_reason"
    if obligation == "act_outside_message":
        return "act_then_confirm" if act_attested else "ask_back"
    if str(row.get("kind") or "") in _REQUIRED_KINDS:
        return "answer_in_message"
    return "answer_in_message"


def verify_obligation(
    draft: str,
    row: dict[str, Any],
    *,
    pin_ledger: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Return ``(status, covering_sentence)`` for one ledger row against ``draft``."""
    quoted = str(row.get("quoted_span") or "")
    obligation = str(row.get("obligation") or "none")
    kind = str(row.get("kind") or "statement")
    act_attested = _effective_act_attested(pin_ledger)
    if obligation == "none":
        return "addressed", ""
    span_tokens = _tokens(quoted)
    body_sentences = _sentences(draft)

    if obligation == "act_outside_message":
        if not act_attested:
            for sentence in body_sentences:
                if _draft_sentence_has_past_action_claim(sentence):
                    return "missing", ""
        for sentence in body_sentences:
            if _act_outside_sentence_covers(sentence, act_attested=act_attested):
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
    draft: str,
    obligations: list[dict[str, Any]],
    *,
    pin_ledger: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build per-obligation coverage rows for ``draft``."""
    act_attested = _effective_act_attested(pin_ledger)
    rows: list[dict[str, Any]] = []
    for row in obligations:
        status, covering = verify_obligation(draft, row, pin_ledger=pin_ledger)
        rows.append(
            {
                "id": row.get("id"),
                "kind": row.get("kind"),
                "obligation": row.get("obligation"),
                "quoted_span": str(row.get("quoted_span") or ""),
                "reply_mode": _reply_mode(row, status, act_attested=act_attested),
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
    pin_ledger: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Blend obligation_coverage audit rows with a fresh deterministic pass."""
    del selected
    actionable = actionable_obligations(obligations)
    fresh = coverage_map_for_draft(selected_body, actionable, pin_ledger=pin_ledger)
    if not audit_map:
        return fresh
    audit_by_id = {str(row.get("id")): row for row in audit_map if row.get("id")}
    return [
        apply_audit_overlay(
            row,
            audit_by_id.get(str(row.get("id") or "")),
            selected_body,
            pin_ledger=pin_ledger,
        )
        for row in fresh
    ]


def obligation_coverage_score(
    draft: str,
    obligations: list[dict[str, Any]],
    *,
    pin_ledger: list[dict[str, Any]] | None = None,
) -> int:
    """Count actionable obligations marked addressed or declined_with_reason."""
    actionable = actionable_obligations(obligations)
    if not actionable:
        return 0
    cov = coverage_map_for_draft(draft, actionable, pin_ledger=pin_ledger)
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
