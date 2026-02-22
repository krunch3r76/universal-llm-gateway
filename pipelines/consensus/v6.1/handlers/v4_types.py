"""
Type definitions for consensus pipeline v4.0 (verified-core).

Simplified from v4.0_abandoned: no skeleton/slot types.
"""

from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict


class RequiredItem(TypedDict):
    """Single item from question analysis."""

    label: str
    required: bool


class QuestionContract(TypedDict):
    """Output of analyze_question — structure hints for synthesis."""

    cleaned_question: str
    question_type: Literal[
        "enumeration",
        "comparison",
        "definition",
        "explanation",
        "simple",
        "proof",
    ]
    required_items: list[RequiredItem]
    cardinality: int
    ordering: Literal["canonical", "alphabetical", "none"]
    structure_notes: NotRequired[str]


class ExpansionSafetyContract(TypedDict):
    """Output of classify_expansion_safety — control signal for gating expansion."""

    expansion_safe: bool
    reason_code: NotRequired[
        Literal["explicit_text_only", "bounded_negative", "open_explanatory"]
    ]


class Candidate(TypedDict):
    """Verifiable claim extracted from model answers."""

    statement_id: str
    text: str
    claim_type: Literal["direct", "supporting"]
    provenance: dict  # From libs/provenance/
    domain: NotRequired[Literal["math", "general"]]
    parent_statement_id: NotRequired[str]
    parent_text: NotRequired[str]


class Evaluation(TypedDict):
    """Single verification verdict."""

    verdict: bool
    reasoning: str


type VerdictEntry = dict[str, str | bool]
"""Per-claim aggregated verdict with reasoning: {"v": bool, "r": str}."""


@dataclass(slots=True, kw_only=True)
class VerificationChunkTiming:
    """Timing for one chunk within a model's verification pass."""

    chunk_index: int
    num_items: int
    latency_ms: float
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(slots=True, kw_only=True)
class VerificationModelTiming:
    """Timing for one model's complete verification pass."""

    model_id: str
    num_claims: int
    latency_ms: float
    mode: str
    chunk_size: int
    chunks: list[VerificationChunkTiming]
    prompt_tokens: int = 0
    completion_tokens: int = 0
