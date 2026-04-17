"""Hybrid heuristic detector for `mcp.frontier.thought.termination.shadow`.

v1 design (agent-bus thread 576): advisory post-hoc detection of silent-failure
patterns in a frontier model's reasoning trace. Never replaces
`generate.completed`, never fires on `generate.error`. Known gap: no semantic
rubric in v1 — phrase-based detection is scaffolding; the semantic detector
becomes the primary defense post-calibration. Provider scope: Gemini only.

Dimensions: phrase (literal refusal/incapacity/policy/scope), position (leading
matches weighted higher), counter-phrase suppression (meta-discussion), n-gram
repetition (loops), token_budget (MAX_TOKENS + non-empty thought).
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from mcp_events import record

_MAX_EXCERPT_CHARS = 120
_LEADING_POSITION_FRACTION = 0.20  # first 20% of thought = "leading"
_REPETITION_NGRAM = 4
_REPETITION_MIN_COUNT = 3

# Reason -> ordered list of regex patterns (lowercase, literal-phrase flavored).
# Word-boundary anchoring keeps "I cannot" from matching inside "significantly".
_REASON_PATTERNS: dict[str, list[str]] = {
    "refusal": [
        r"\bi\s+cannot\b",
        r"\bi\s+can'?t\s+(help|assist|do|provide|comply)",
        r"\bi\s+will\s+not\b",
        r"\bi\s+won'?t\b",
        r"\bi\s+refuse\b",
        r"\bi'?m\s+not\s+(able|going|willing)\s+to\b",
    ],
    "incapacity": [
        r"\bi\s+am\s+unable\b",
        r"\bi'?m\s+unable\b",
        r"\bi\s+do\s+not\s+have\s+(access|the\s+ability|information)\b",
        r"\bi\s+lack\s+(access|the\s+ability)\b",
        r"\bi\s+cannot\s+access\b",
    ],
    "policy": [
        r"\bas\s+(a|an)\s+(large\s+language\s+model|ai\s+(model|assistant))\b",
        r"\bagainst\s+my\s+(guidelines|policies|programming)\b",
        r"\bmy\s+(safety\s+)?guidelines\b",
        r"\bi\s+cannot\s+provide\s+(financial|legal|medical)\s+advice\b",
        r"\bnot\s+appropriate\s+for\s+me\s+to\b",
    ],
    "scope": [
        r"\bbeyond\s+my\s+(scope|capabilities|knowledge)\b",
        r"\boutside\s+my\s+(scope|domain|expertise)\b",
        r"\bi\s+do\s+not\s+have\s+(access\s+to\s+)?real[- ]?time\b",
        r"\bi\s+don'?t\s+have\s+(access\s+to\s+)?real[- ]?time\b",
        r"\bmy\s+(training|knowledge)\s+(cutoff|data)\b",
    ],
}

# Counter-phrases. If ANY of these appears within +/-80 chars of a positive
# match, that match is suppressed — it's almost certainly meta-discussion, not
# a performative refusal.
_COUNTER_PHRASES: list[str] = [
    "for example",
    "hypothetically",
    "such as when",
    "in a case where",
    "if i were to",
    "one might say",
    "the model might",
    "you might see",
    "a typical refusal",
    "example of a refusal",
    "this is different from",
    "as opposed to",
]

_COUNTER_WINDOW = 80


@dataclass
class TerminationEvidence:
    kind: str  # "phrase" | "position" | "repetition" | "token_budget"
    score: float
    excerpt: str


@dataclass
class TerminationDetection:
    reason: str
    confidence: float
    evidence: list[TerminationEvidence]
    suggested_next_action: str
    trace_visibility: str  # "full" | "partial" | "none"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _excerpt(text: str, start: int, end: int) -> str:
    raw = text[max(0, start - 20) : min(len(text), end + 20)]
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw[:_MAX_EXCERPT_CHARS]


def _has_counter_phrase_near(text: str, pos: int) -> bool:
    window_start = max(0, pos - _COUNTER_WINDOW)
    window_end = min(len(text), pos + _COUNTER_WINDOW)
    window = text[window_start:window_end]
    return any(cp in window for cp in _COUNTER_PHRASES)


def _find_phrase_matches(
    thinking_lower: str,
) -> tuple[str | None, list[TerminationEvidence]]:
    """Scan thought text for refusal patterns; return (best_reason, evidence)."""
    reason_scores: Counter[str] = Counter()
    evidence: list[TerminationEvidence] = []
    text_len = max(1, len(thinking_lower))

    for reason, patterns in _REASON_PATTERNS.items():
        for pat in patterns:
            for m in re.finditer(pat, thinking_lower):
                start = m.start()
                if _has_counter_phrase_near(thinking_lower, start):
                    continue
                position_fraction = start / text_len
                is_leading = position_fraction <= _LEADING_POSITION_FRACTION
                phrase_score = 0.55 + (0.25 if is_leading else 0.0)
                reason_scores[reason] += 1
                evidence.append(
                    TerminationEvidence(
                        kind="phrase",
                        score=round(phrase_score, 3),
                        excerpt=_excerpt(thinking_lower, start, m.end()),
                    )
                )
                if is_leading:
                    evidence.append(
                        TerminationEvidence(
                            kind="position",
                            score=round(1.0 - position_fraction, 3),
                            excerpt=f"match at {position_fraction:.1%} of thought",
                        )
                    )
                break  # one match per pattern is enough

    best = reason_scores.most_common(1)
    return (best[0][0] if best else None), evidence


def _detect_repetition(thinking_lower: str) -> TerminationEvidence | None:
    words = thinking_lower.split()
    if len(words) < _REPETITION_NGRAM * _REPETITION_MIN_COUNT:
        return None
    ngrams = [
        " ".join(words[i : i + _REPETITION_NGRAM])
        for i in range(len(words) - _REPETITION_NGRAM + 1)
    ]
    c = Counter(ngrams)
    top, count = c.most_common(1)[0]
    if count < _REPETITION_MIN_COUNT:
        return None
    # Score rises with count; cap at 0.9.
    score = min(0.9, 0.5 + 0.1 * (count - _REPETITION_MIN_COUNT))
    return TerminationEvidence(
        kind="repetition",
        score=round(score, 3),
        excerpt=f"'{top[:60]}' x{count}",
    )


def detect_termination(
    *,
    thinking_text: str | None,
    content: str,
    finish_reason: str | None,
    output_tokens: int,
) -> TerminationDetection | None:
    """Detect likely silent-termination patterns. Returns None if no signal.

    ``thinking_text`` is the provider's surfaced reasoning (Gemini thought
    summaries when includeThoughts=True). Without it, the detector has no
    substrate and returns None — by design, not a bug.
    """
    if not thinking_text or not thinking_text.strip():
        return None

    thinking_lower = thinking_text.lower()
    trace_visibility = "partial"  # Gemini exposes summaries, not full CoT

    reason, evidence = _find_phrase_matches(thinking_lower)
    rep_ev = _detect_repetition(thinking_lower)
    if rep_ev is not None:
        evidence.append(rep_ev)
        if reason is None:
            reason = "loop"

    # Token-budget exhaustion: MAX_TOKENS with non-trivial thinking + short
    # content = model ran out of budget mid-reasoning, not substantive complete.
    if finish_reason == "MAX_TOKENS" and len(content) < 500:
        evidence.append(
            TerminationEvidence(
                kind="token_budget",
                score=0.85,
                excerpt=f"finish_reason=MAX_TOKENS, output_tokens={output_tokens}",
            )
        )
        if reason is None:
            reason = "token_exhaustion"

    if reason is None or not evidence:
        return None

    confidence = min(1.0, max(e.score for e in evidence))
    if len(evidence) > 1:
        # Multiple independent signals → small boost, capped.
        confidence = min(1.0, confidence + 0.05 * (len(evidence) - 1))

    suggested = {
        "refusal": "escalate_to_user",
        "incapacity": "switch_model",
        "policy": "escalate_to_user",
        "scope": "retry_with_context",
        "token_exhaustion": "retry_with_context",
        "loop": "switch_model",
        "coherence_collapse": "switch_model",
        "user_directed": "none",
    }.get(reason, "none")

    return TerminationDetection(
        reason=reason,
        confidence=round(confidence, 3),
        evidence=evidence,
        suggested_next_action=suggested,
        trace_visibility=trace_visibility,
    )


def emit_termination_shadow_if_detected(
    result: dict[str, Any],
    tool_name: str,
    api_model: str,
    provider_key: str,
    boot_level: str,
    output_tokens: int,
    finish_reason: str | None,
    block_reason: str | None,
) -> None:
    """Gate + detect + emit `mcp.frontier.thought.termination.shadow`.

    v1 scope: provider=google AND boot_level ∈ {team, full}. `generate_id`
    is minted here at the execute_frontier retry boundary (API Claude mod
    #2, thread 576 t5) — a retry inside the adapter cannot fork it.
    """
    provider = result.get("provider", provider_key)
    if provider != "google" or boot_level not in ("team", "full"):
        return
    thinking_obj = result.get("thinking") or {}
    thinking_text = thinking_obj.get("text") if isinstance(thinking_obj, dict) else None
    detection = detect_termination(
        thinking_text=thinking_text,
        content=result.get("content") or "",
        finish_reason=finish_reason,
        output_tokens=output_tokens,
    )
    if detection is None:
        return
    record(
        "mcp.frontier.thought.termination.shadow",
        tool=tool_name,
        provider=provider,
        model=result.get("model", api_model),
        boot_level=boot_level,
        output_tokens=output_tokens,
        finish_reason=finish_reason,
        block_reason=block_reason,
        generate_id=str(uuid.uuid4()),
        detector={
            "mode": "hybrid",
            "version": "v1",
            "provider": provider,
            "adapter": "llm_adapter_google",
        },
        **detection.to_dict(),
    )
