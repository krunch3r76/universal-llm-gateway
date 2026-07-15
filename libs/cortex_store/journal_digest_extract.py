"""Journal digest EXTRACT+CLASSIFY — atomic claims with provenance from narrative."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from universal_logging import get_logger

logger = get_logger("cortex-api.journal_digest_extract")

DIGEST_EXTRACT_MODEL = os.environ.get("CORTEX_DIGEST_EXTRACT_MODEL", "")

STARGATE_URL = "http://localhost:9999"


def _max_tokens() -> int:
    return int(os.environ.get("CORTEX_DIGEST_MAX_TOKENS", "16384"))


def _request_timeout() -> float:
    return float(os.environ.get("CORTEX_DIGEST_TIMEOUT_S", "180"))

_VALID_P_CLASSES = frozenset({"P1", "P2", "P3", "P2²"})
_VALID_CANONICALITY = frozenset({"assert", "prose"})
_REQUIRED_CLAIM_KEYS = frozenset(
    {"claim", "p_class", "canonicality", "attach_hint", "flags", "evidence_anchor"}
)

_EXTRACT_SYSTEM = """\
You decompose operator journal narrative into atomic claims for a knowledge graph.

## Provenance classes (exactly one per claim; embed in claim text)

| Class | Definition | Claim-text shape |
|---|---|---|
| P1 | Operator observed/received/witnessed first-hand | "Operator scheduled/received/called X" — no attribution wrapper |
| P2 | Named party told operator X; only the saying is confirmed | "«Role/name» stated X" — X never bare |
| P3 | Operator's own inference | "Operator infers X because Y; Z unverified" |
| P2² | Nested reported-about-reported (e.g. A stated A told B X) | BOTH hops attributed in claim text |

Rules:
- P2 figure and P1 dispute coexist as separate claims — never adjudicate.
- Preserve uncertainty markers (e.g. "Michael (?)") — add flag name_uncertain.
- Preserve garbled phrasing — add flag phrasing_ambiguous; never smooth.
- Verbal vs written deadline conflicts — add flag deadline_conflict on affected rows.

## Canonicality (assert vs prose)

ASSERT: counterparty identities/roles; reference/case numbers; scheduled payments;
appointments/deadlines; monetary determinations; formal notices; commitments/conditions;
contact info; operator disputes; policy/coverage state changes.
PROSE: synthesis, strategy, impressions, counsel to third parties, speculation without
operational weight, routine log-class instances.
Threshold: state-changes assert; routine instances roll up to prose.

## Output

Return a JSON object with key "claims" — an array of objects, each with:
- claim (str): atomic, ≤1–2 sentences, provenance embedded per class rules
- p_class (str): P1 | P2 | P3 | P2²
- canonicality (str): assert | prose
- attach_hint (str|null): subject phrase for later entity resolve
- flags (list[str]): e.g. name_uncertain, phrasing_ambiguous, deadline_conflict
- evidence_anchor (str): subsection slug within the entry

Return ONLY valid JSON. No markdown fences, no commentary."""


def _get_model() -> str:
    """Resolve the model for digest extraction."""
    if DIGEST_EXTRACT_MODEL:
        return DIGEST_EXTRACT_MODEL
    return ""


def _chat_completion(system: str, user: str) -> str | None:
    """Call Stargate chat completions. Returns content or None."""
    model = _get_model()
    if not model:
        logger.warning(
            "No digest extract model configured (CORTEX_DIGEST_EXTRACT_MODEL empty)"
        )
        return None

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": _max_tokens(),
        "temperature": 0.3,
    }

    try:
        with httpx.Client(timeout=_request_timeout()) as client:
            resp = client.post(
                f"{STARGATE_URL}/v1/chat/completions",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception:
        logger.warning("Digest extract LLM call failed", exc_info=True)
        return None


def strip_json_fences(text: str) -> str:
    """Remove markdown code fences from model output."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    return cleaned


def validate_claim(raw: Any) -> dict[str, Any] | None:
    """Return a normalized claim dict or None if invalid."""
    if not isinstance(raw, dict):
        return None

    if not _REQUIRED_CLAIM_KEYS.issubset(raw.keys()):
        return None

    claim = raw["claim"]
    p_class = raw["p_class"]
    canonicality = raw["canonicality"]
    attach_hint = raw["attach_hint"]
    flags = raw["flags"]
    evidence_anchor = raw["evidence_anchor"]

    if not isinstance(claim, str) or not claim.strip():
        return None
    if p_class not in _VALID_P_CLASSES:
        return None
    if canonicality not in _VALID_CANONICALITY:
        return None
    if attach_hint is not None and not isinstance(attach_hint, str):
        return None
    if not isinstance(flags, list) or not all(isinstance(f, str) for f in flags):
        return None
    if not isinstance(evidence_anchor, str) or not evidence_anchor.strip():
        return None

    return {
        "claim": claim.strip(),
        "p_class": p_class,
        "canonicality": canonicality,
        "attach_hint": attach_hint,
        "flags": flags,
        "evidence_anchor": evidence_anchor.strip(),
    }


def parse_claim_batch(
    raw_text: str,
    *,
    entry_anchor: str,
    journal_uri: str,
) -> dict[str, Any] | None:
    """Parse and validate a claim-batch JSON string into the envelope dict."""
    cleaned = strip_json_fences(raw_text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Claim extraction returned invalid JSON: %.200s", cleaned)
        return None

    if not isinstance(parsed, dict):
        logger.warning("Claim extraction returned non-object: %s", type(parsed))
        return None

    raw_claims = parsed.get("claims")
    if not isinstance(raw_claims, list):
        logger.warning("Claim extraction missing claims array")
        return None

    valid_claims: list[dict[str, Any]] = []
    rejected = 0
    for item in raw_claims:
        validated = validate_claim(item)
        if validated is None:
            rejected += 1
            continue
        valid_claims.append(validated)

    if rejected:
        logger.warning("Claim extraction rejected %d invalid row(s)", rejected)

    if not valid_claims:
        logger.warning("Claim extraction produced zero valid claims")
        return None

    return {
        "entry_anchor": entry_anchor,
        "journal_uri": journal_uri,
        "claims": valid_claims,
    }


def extract_claims(
    entry_text: str,
    *,
    entry_anchor: str,
    journal_uri: str,
) -> dict[str, Any] | None:
    """Return validated claim-batch dict or None on model/parse failure."""
    user_prompt = (
        f"Journal URI: {journal_uri}\n"
        f"Entry anchor: {entry_anchor}\n\n"
        f"Entry text:\n{entry_text}"
    )
    result = _chat_completion(_EXTRACT_SYSTEM, user_prompt)
    if result is None:
        return None

    return parse_claim_batch(
        result,
        entry_anchor=entry_anchor,
        journal_uri=journal_uri,
    )
