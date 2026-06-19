"""Assertion quality enforcement — v2.4 validation middleware.

Computes quality_score on ingest and enforces provenance/temporal rules.
Called from the create_assertion handler before INSERT.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import AssertionCreate

_DATE_PATTERN = re.compile(
    r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},?\s+\d{4}\b"
    r"|\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4}\b",
    re.IGNORECASE,
)

_SOURCED_TYPES = frozenset({"quotation", "compression"})
_THREAD_COMPRESSION_TYPES = frozenset({"thread_compression"})
_OBSERVATION_TYPES = frozenset(
    {"agent_observation", "direct_observation", "user_statement"}
)

# Taxonomy surfaced in 422 bodies so failed writes carry the full valid set
# + per-type co-requirements inline. Symmetric with /edges 422 valid_types.
DERIVATION_TYPE_TAXONOMY: dict[str, dict[str, object]] = {
    "inference": {
        "description": "agent synthesis from prior context or reasoning chain",
        "requires": [],
    },
    "user_statement": {
        "description": "claim the user told you directly in conversation",
        "requires": [],
    },
    "agent_observation": {
        "description": "direct observation from tool output or runtime behavior",
        "requires": [],
    },
    "direct_observation": {
        "description": "structural/deterministic read (schema, filesystem, config)",
        "requires": [],
    },
    "compression": {
        "description": "compressed from ingested document chunks",
        "requires": ["chunk_id", "evidence_uris"],
    },
    "thread_compression": {
        "description": "thread compaction summary from workspace turn artifacts",
        "requires": ["evidence_uris"],
    },
    "quotation": {
        "description": "verbatim quote from an ingested document chunk",
        "requires": ["chunk_id", "evidence_uris"],
    },
    "commitment": {
        "description": "promise or commitment made by an agent or user",
        "requires": [],
    },
    "stated": {
        "description": "stated claim (less structured than user_statement)",
        "requires": [],
    },
    "other": {
        "description": "none of the above — reasoning_summary strongly recommended",
        "requires": [],
    },
}


@dataclass
class ValidationDiagnostic:
    field: str
    message: str
    # Discriminator for the downstream `_next` hint logic — staging vs auditor
    # warnings should produce different hints. Default "staging" preserves the
    # historical category of every diagnostic this module emitted before the
    # auditor-validatability checks landed.
    category: str = "staging"


@dataclass
class ValidationResult:
    quality_score: float
    hard_reject: list[ValidationDiagnostic] = field(default_factory=list)
    warnings: list[ValidationDiagnostic] = field(default_factory=list)
    route_to_staging: bool = False

    @property
    def rejected(self) -> bool:
        return len(self.hard_reject) > 0


def _has_date_pattern(text: str) -> bool:
    return bool(_DATE_PATTERN.search(text))


def compute_quality_score(body: AssertionCreate) -> float:
    """Compute assertion quality score (0.0–1.0).

    Weighting: 40% provenance, 30% temporal, 30% reasoning.
    """
    provenance = 0.0
    if body.derivation_type:
        provenance += 0.4
    if body.chunk_id is not None:
        provenance += 0.3
    if body.evidence_uris:
        provenance += 0.3

    temporal = 0.0
    has_dates = _has_date_pattern(body.claim)
    if body.valid_from:
        temporal += 0.5
    elif not has_dates:
        temporal += 0.5
    if body.observed_at:
        temporal += 0.5

    reasoning = 0.0
    if body.reasoning_summary:
        reasoning += 0.5
    if body.evidence and len(body.evidence) > 10:
        reasoning += 0.5

    return round(0.4 * provenance + 0.3 * temporal + 0.3 * reasoning, 3)


def validate_assertion(body: AssertionCreate) -> ValidationResult:
    """Validate an assertion against v2.4 quality rules.

    Hard rejects return specific diagnostics. Warnings route to staging.
    Agent observations (observe/friction tools) get relaxed validation —
    they skip chunk/evidence_uris requirements and auto-default observed_at.
    """
    is_observation = body.derivation_type in _OBSERVATION_TYPES
    score = compute_quality_score(body)
    if is_observation and score < 0.7:
        score = max(score, 0.7)
    result = ValidationResult(quality_score=score)

    if not body.derivation_type:
        result.hard_reject.append(
            ValidationDiagnostic(
                field="derivation_type",
                message="derivation_type is required — specify how this claim was derived. "
                "Types that require chunk_id + evidence_uris: quotation, compression. "
                "Types that do NOT require chunk_id (session/observation sources): "
                "user_statement, agent_observation, direct_observation, inference, "
                "commitment, stated, other.",
            )
        )

    if body.derivation_type in _THREAD_COMPRESSION_TYPES:
        if not body.evidence_uris:
            result.hard_reject.append(
                ValidationDiagnostic(
                    field="evidence_uris",
                    message="derivation_type='thread_compression' requires non-empty "
                    "evidence_uris linking workspace turn artifacts",
                )
            )
        if body.chunk_id is not None:
            result.hard_reject.append(
                ValidationDiagnostic(
                    field="chunk_id",
                    message="derivation_type='thread_compression' must not set chunk_id "
                    "(document-ingestion chunk semantics are for compression/quotation)",
                )
            )

    if body.derivation_type in _SOURCED_TYPES:
        if body.chunk_id is None:
            result.hard_reject.append(
                ValidationDiagnostic(
                    field="chunk_id",
                    message=f"derivation_type={body.derivation_type!r} requires chunk_id — "
                    "chunk_id is the RAG-deterministic ID of the form "
                    "'{content_hash_prefix}-{i}'. For session-originated claims: "
                    "use 'inference' for agent synthesis from prior context, or "
                    "'user_statement' for claims the user told you directly.",
                )
            )
        if not body.evidence_uris:
            result.hard_reject.append(
                ValidationDiagnostic(
                    field="evidence_uris",
                    message=f"derivation_type={body.derivation_type!r} requires non-empty "
                    "evidence_uris linking back to the source document",
                )
            )

    if _has_date_pattern(body.claim) and not body.valid_from and not is_observation:
        result.hard_reject.append(
            ValidationDiagnostic(
                field="valid_from",
                message="Claim contains date patterns but valid_from is absent — "
                "set valid_from to the date the claimed fact became true",
            )
        )

    if not body.observed_at and not is_observation:
        result.hard_reject.append(
            ValidationDiagnostic(
                field="observed_at",
                message="observed_at is required — when was this fact observed or recorded?",
            )
        )

    if not body.reasoning_summary:
        result.warnings.append(
            ValidationDiagnostic(
                field="reasoning_summary",
                message="reasoning_summary absent — especially important for inference-type assertions",
            )
        )
        result.route_to_staging = True

    if body.evidence_uris and body.chunk_id is None:
        result.warnings.append(
            ValidationDiagnostic(
                field="chunk_id",
                message="evidence_uris present but chunk_id null — sourced but unchunked; "
                "supply chunk_id (RAG-deterministic '{content_hash_prefix}-{i}') "
                "when the source has been indexed by RAG",
            )
        )
        result.route_to_staging = True

    if score < 0.7:
        result.route_to_staging = True

    return result


# ---------------------------------------------------------------------------
# Auditor-validatability warnings (Checks 1–3)
# ---------------------------------------------------------------------------

# ∀ quoted string ≥15 chars: any of standard quote styles counts as verbatim.
# Five alternatives: ASCII double, ASCII single, Unicode curly double
# (U+201C/U+201D), French guillemets, Unicode curly single (U+2018/U+2019).
# {15,} matches the spec threshold literally — a 14-char quoted span should
# not satisfy the auditor's verbatim requirement.
_VERBATIM_RE = re.compile(
    r'"[^"]{15,}"'
    r"|\'[^\']{15,}\'"
    r"|\u201c[^\u201d]{15,}\u201d"
    r"|«[^»]{15,}»"
    r"|\u2018[^\u2019]{15,}\u2019"
)

# derivation types where we expect verbatim source text in the claim
_VERBATIM_EXPECTED_TYPES = frozenset(
    {"direct_observation", "quotation", "agent_observation"}
)

_AUDITOR_SKILL_REF = (
    "See agent_skill:auditor-validatable-confidence for full discipline."
)


def check_confirmed_validatability(
    confidence: str,
    evidence_uris: list[str] | None,
    derivation_type: str | None,
    claim: str,
    acknowledge_audit_gaps: list[str] | None = None,
) -> list[dict[str, str]]:
    """Advisory auditor-validatability checks for confidence='confirmed' assertions.

    Symmetric to the existing chunk_id sourced-but-unchunked warning — same
    response surface (validation_warnings), advisory only, never rejects.
    Returns [] when confidence is not 'confirmed' or all checks pass.

    acknowledge_audit_gaps: pass one or more of ['no_evidence_uris',
    'inference_confirmed', 'no_verbatim'] to suppress the corresponding check
    when the agent has documented intent (e.g. structural claim, already
    confirmed by other means). Suppression is explicit — never silent.

    Operationalises Kaywan's auditor-validatability principle (assertion 9715
    on document:entity-backed-claim-provenance-v1): whatever entity you
    designate confirmed, an independent auditor (LLM) should be able to
    validate it from the entity card alone.
    """
    if confidence != "confirmed":
        return []

    ack = set(acknowledge_audit_gaps or [])
    warnings: list[dict[str, str]] = []

    # Check 1 — confirmed + no evidence_uris
    if not evidence_uris and "no_evidence_uris" not in ack:
        warnings.append(
            {
                "field": "evidence_uris",
                "category": "auditor",
                "message": (
                    "confidence:confirmed assertion has no evidence_uris — auditor cannot "
                    "independently verify; add a URI or downgrade to believed. "
                    "Pass acknowledge_audit_gaps=['no_evidence_uris'] to suppress. "
                    + _AUDITOR_SKILL_REF
                ),
            }
        )

    # Check 2 — confirmed + inference derivation_type
    if derivation_type == "inference" and "inference_confirmed" not in ack:
        warnings.append(
            {
                "field": "derivation_type",
                "category": "auditor",
                "message": (
                    "confidence:confirmed with derivation_type:inference is unusual — "
                    "inference typically supports believed/suspected. If this is "
                    "direct_observation or agent_observation, fix derivation_type. "
                    "If genuinely inferential, downgrade confidence to believed. "
                    "Pass acknowledge_audit_gaps=['inference_confirmed'] to suppress. "
                    + _AUDITOR_SKILL_REF
                ),
            }
        )

    # Check 3 — confirmed + verbatim-expected type + evidence present + no quoted string
    if (
        derivation_type in _VERBATIM_EXPECTED_TYPES
        and evidence_uris
        and "no_verbatim" not in ack
        and not _VERBATIM_RE.search(claim)
    ):
        warnings.append(
            {
                "field": "claim",
                "category": "auditor",
                "message": (
                    "confidence:confirmed claim has no embedded verbatim quote ≥15 chars; "
                    "auditor needs the literal source text to verify against evidence_uris. "
                    "Embed the quote in quote marks, or downgrade, or pass "
                    "acknowledge_audit_gaps=['no_verbatim'] for structural claims. "
                    + _AUDITOR_SKILL_REF
                ),
            }
        )

    return warnings


# ---------------------------------------------------------------------------
# Claim-brevity advisory (friction 16982 — brief-claim + sidecar pattern)
# ---------------------------------------------------------------------------

# Soft cap on inline claim length. Above this, the assertion is shaped like a
# document, not an index entry — the body belongs in a Cortex sidecar with the
# claim reduced to a one/two-sentence summary + evidence_uris pointing at it.
_CLAIM_BREVITY_THRESHOLD = 300


def check_claim_brevity(
    claim: str,
    evidence_uris: list[str] | None,
    entity_id: str | None = None,
    acknowledge_audit_gaps: list[str] | None = None,
) -> list[dict[str, str]]:
    """Advisory check: long inline claim with no sidecar (friction 16982).

    Mirrors the existing reasoning_summary / auditor warning surface — appended
    to validation_warnings, advisory only, never rejects and never routes to
    staging. Fires only when the claim exceeds the brevity threshold AND no
    evidence_uris point at a sidecar: a long claim that already references a
    sidecar via evidence_uris is the desired shape, not a violation.

    Suppressed for ``service:`` entities whose claims begin with ``[`` (friction
    observation pattern) — these entities are consumed by LLM ranking pipelines
    (skill_suggest, etc.) that use the full claim text for matching. Shortening
    those claims would degrade ranking quality rather than improve it (#20155).

    Per-assert opt-out: pass ``acknowledge_audit_gaps=['long_claim_beneficial']``
    when the caller has a documented reason to keep the claim verbose (e.g. the
    claim body directly feeds an LLM ranking or retrieval pipeline).

    The brief-claim + sidecar pattern (analogous to the agent-bus brief-body
    convention): keep the claim to a one/two-sentence index entry; move prose,
    code blocks, and numbered detail into a Cortex sidecar referenced by
    evidence_uris. Long claims inflate context on every entity_get, search hit,
    and boot-card surface permanently. See agent_skill:cortex-orientation.
    """
    if len(claim) <= _CLAIM_BREVITY_THRESHOLD or evidence_uris:
        return []

    # Context-sensitive suppression: service: entities with friction-style claims
    # feed LLM ranking pipelines where claim verbosity improves matching quality.
    if (
        entity_id
        and entity_id.startswith("service:")
        and claim.lstrip().startswith("[")
    ):
        return []

    # Per-assert opt-out via acknowledge_audit_gaps.
    ack = set(acknowledge_audit_gaps or [])
    if "long_claim_beneficial" in ack:
        return []

    return [
        {
            "field": "claim",
            "category": "brevity",
            "message": (
                f"claim is {len(claim)} chars (>{_CLAIM_BREVITY_THRESHOLD}) with no "
                "evidence_uris — long inline claims inflate context on every "
                "entity_get, search hit, and boot-card surface permanently. "
                "Shorten the claim to a one/two-sentence summary, move the detail "
                "to a Cortex sidecar, and set evidence_uris to point at it "
                "(brief-claim + sidecar pattern). To suppress when verbosity is "
                "intentional, pass acknowledge_audit_gaps=['long_claim_beneficial']. "
                "See agent_skill:cortex-orientation."
            ),
        }
    ]
