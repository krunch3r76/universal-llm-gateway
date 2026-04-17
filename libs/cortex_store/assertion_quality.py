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
_OBSERVATION_TYPES = frozenset(
    {"agent_observation", "direct_observation", "user_statement"}
)


@dataclass
class ValidationDiagnostic:
    field: str
    message: str


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

    if body.derivation_type in _SOURCED_TYPES:
        if body.chunk_id is None:
            result.hard_reject.append(
                ValidationDiagnostic(
                    field="chunk_id",
                    message=f"derivation_type={body.derivation_type!r} requires chunk_id — "
                    "create chunks via POST /chunks or cortex_ingest_document() first. "
                    "For session-originated claims (user told you in conversation), "
                    "use derivation_type='user_statement' instead.",
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
                "consider using cortex_ingest_document() for proper chunking",
            )
        )
        result.route_to_staging = True

    if score < 0.7:
        result.route_to_staging = True

    return result
