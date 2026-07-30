"""Action-typed predicate enrichment templates (slice 2).

Rule-based extraction from claim text for denied/request/granted/pending
patterns. Dry-run only — never writes to live assertion rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .action_vocabulary import ACTION_VOCAB_V0, ActionPredicate, party_from_entity_id

# Claim-text → controlled action enum (v0 seed from escrow case).
_ACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"spread(?:\s+the)?\s+escrow\s+shortage|"
            r"escrow\s+shortage\s+spread|"
            r"spread\s+extension|"
            r"extend\s+escrow\s+shortage\s+spread",
            re.I,
        ),
        "spread_extension",
    ),
    (
        re.compile(
            r"lower[\s-]?payment|payment\s+reduction|reduce\s+payment",
            re.I,
        ),
        "payment_reduction",
    ),
    (
        re.compile(r"escrow\s+analysis", re.I),
        "escrow_analysis",
    ),
    (
        re.compile(r"loan\s+modification", re.I),
        "loan_modification",
    ),
    (
        re.compile(r"hardship\s+program", re.I),
        "hardship_program",
    ),
)

_DENIED_RE = re.compile(
    r"\b(?:was\s+)?denied\b|\bunable\s+to\b|\bcan(?:no)?t\b.*\bspread\b",
    re.I,
)
_GRANTED_RE = re.compile(r"\b(?:was\s+)?granted\b|\bapproved\b", re.I)
_PENDING_RE = re.compile(r"\b(?:pending|opened|requested)\b", re.I)
_REQUEST_RE = re.compile(r"\b(?:request(?:ed|ing)?|ask(?:ed|ing)?)\b", re.I)

_DATE_RE = re.compile(
    r"\b(20\d{2}-\d{2}-\d{2})\b|"
    r"\b(?:on\s+)?(?:the\s+)?(\d{4}-\d{2}-\d{2})\b|"
    r"\b(\d{1,2}/\d{1,2}/\d{4})\b",
)
_WO_RE = re.compile(r"\bWO\s*#?\s*(\d+)\b", re.I)


@dataclass(frozen=True)
class EnrichmentPreview:
    assertion_id: int | None
    entity_id: str
    predicate_form: str
    functor: str
    action: str
    party: str
    epistemic_state: str | None
    source: str


def _detect_action(claim: str) -> str | None:
    for pattern, action in _ACTION_PATTERNS:
        if pattern.search(claim):
            return action
    return None


def _detect_date(claim: str, observed_at: str | None = None) -> str | None:
    for match in _DATE_RE.finditer(claim):
        for group in match.groups():
            if not group:
                continue
            if "/" in group:
                parts = group.split("/")
                if len(parts) == 3:
                    month, day, year = parts
                    return f"{year}-{int(month):02d}-{int(day):02d}"
            return group
    if observed_at and len(observed_at) >= 10:
        return observed_at[:10]
    return None


def _detect_functor(claim: str) -> str | None:
    if _DENIED_RE.search(claim):
        return "denied"
    if _GRANTED_RE.search(claim):
        return "granted"
    if _PENDING_RE.search(claim):
        return "pending"
    if _REQUEST_RE.search(claim):
        return "request"
    return None


def enrich_action_predicate_from_claim(
    claim: str,
    entity_id: str,
    *,
    assertion_id: int | None = None,
    observed_at: str | None = None,
    epistemic_state: str | None = None,
) -> EnrichmentPreview | None:
    """Extract an action-typed predicate_form preview from claim text (read-only)."""
    action = _detect_action(claim)
    functor = _detect_functor(claim)
    party = party_from_entity_id(entity_id)
    if not action or not functor or not party:
        return None
    if action not in ACTION_VOCAB_V0:
        return None

    date = _detect_date(claim, observed_at)
    wo_match = _WO_RE.search(claim)
    wo_id = wo_match.group(1) if wo_match else None

    if functor == "request":
        pred = ActionPredicate(
            functor="request",
            action=action,
            party=party,
            assertion_id=assertion_id,
            epistemic_state=epistemic_state,
        )
    elif functor == "pending":
        pred = ActionPredicate(
            functor="pending",
            action=action,
            party=party,
            wo_id=wo_id,
            assertion_id=assertion_id,
            epistemic_state=epistemic_state,
        )
    else:
        pred = ActionPredicate(
            functor=functor,  # type: ignore[arg-type]
            action=action,
            party=party,
            date=date,
            assertion_id=assertion_id,
            epistemic_state=epistemic_state,
        )

    return EnrichmentPreview(
        assertion_id=assertion_id,
        entity_id=entity_id,
        predicate_form=pred.to_predicate_form(),
        functor=functor,
        action=action,
        party=party,
        epistemic_state=epistemic_state,
        source="action_enrichment_template_v0",
    )


def dry_run_enrich_assertions(
    rows: list[dict],
) -> list[EnrichmentPreview]:
    """Dry-run enrichment over assertion-shaped dicts (claim + entity_id + id)."""
    previews: list[EnrichmentPreview] = []
    for row in rows:
        claim = row.get("claim") or ""
        entity_id = row.get("entity_id") or ""
        preview = enrich_action_predicate_from_claim(
            claim,
            entity_id,
            assertion_id=row.get("id"),
            observed_at=row.get("observed_at"),
            epistemic_state=row.get("review_status") or row.get("confidence"),
        )
        if preview is not None:
            previews.append(preview)
    return previews
