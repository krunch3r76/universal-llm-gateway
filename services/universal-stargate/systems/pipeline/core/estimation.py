"""Shared token-budget estimation helpers for pipeline batching and cost planning.

Pure, dependency-free arithmetic: character-to-token estimation, first-fit-decreasing
bin packing of items into token-budgeted batches, the code-review validate-step token
envelope, and per-model cost projection. The ``POST /pipelines/estimate`` router
(``proxy/routers/v1/pipeline_estimate.py``) is the caller. All helpers validate inputs
eagerly and raise ``ValueError`` on negative or non-positive parameters; token counts
are always rounded up with ``ceil``.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import TypedDict


class PackedBatch(TypedDict):
    """One batch produced by ``pack_first_fit_decreasing``: item names plus token total.

    ``items`` lists ``EstimateItem.name`` values in placement order and ``tokens`` is
    their summed estimate, which never exceeds the budget unless a single oversize item
    forced its own batch. Returned as plain dicts for direct JSON serialization.
    """

    items: list[str]
    tokens: int


@dataclass(slots=True, kw_only=True)
class EstimateItem:
    """Single item to estimate and batch."""

    name: str
    chars: int
    tokens: int


def estimate_tokens(chars: int, *, chars_per_token: float) -> int:
    """Convert a character count into an approximate token count, rounded up.

    Computes ``ceil(chars / chars_per_token)``; the ratio is caller-supplied per
    pipeline. Raises ``ValueError`` when ``chars`` is negative or ``chars_per_token`` is
    not positive.
    """
    if chars < 0:
        raise ValueError("chars must be >= 0")
    if chars_per_token <= 0:
        raise ValueError("chars_per_token must be > 0")
    return int(ceil(chars / chars_per_token))


def pack_first_fit_decreasing(
    items: list[EstimateItem],
    *,
    budget_tokens: int,
) -> list[PackedBatch]:
    """Group items into token-budgeted batches with the first-fit-decreasing heuristic.

    Items are sorted by ``tokens`` descending and each is placed in the first existing
    batch with room, else a new batch. An item larger than ``budget_tokens`` still gets
    its own (over-budget) batch rather than being dropped. Returns a list of
    ``PackedBatch``. Raises ``ValueError`` when ``budget_tokens`` is not positive.
    """
    if budget_tokens <= 0:
        raise ValueError("budget_tokens must be > 0")

    ordered = sorted(items, key=lambda item: item.tokens, reverse=True)
    batches: list[PackedBatch] = []
    for item in ordered:
        placed = False
        for batch in batches:
            if batch["tokens"] + item.tokens <= budget_tokens:
                batch["items"].append(item.name)
                batch["tokens"] += item.tokens
                placed = True
                break
        if not placed:
            batches.append({"items": [item.name], "tokens": item.tokens})
    return batches


def compute_code_review_validate_tokens(
    source_tokens: int,
    *,
    validate_amplification: float,
    fixed_overhead_tokens: int,
) -> int:
    """Estimate the validate-step token envelope for the code-review pipeline.

    Formula: ``ceil(source_tokens * validate_amplification + fixed_overhead_tokens)``,
    modelling validation output that scales with reviewed source plus a fixed prompt
    cost. Raises ``ValueError`` on negative tokens/overhead or a non-positive
    amplification.
    """
    if source_tokens < 0:
        raise ValueError("source_tokens must be >= 0")
    if validate_amplification <= 0:
        raise ValueError("validate_amplification must be > 0")
    if fixed_overhead_tokens < 0:
        raise ValueError("fixed_overhead_tokens must be >= 0")
    return int(ceil(source_tokens * validate_amplification + fixed_overhead_tokens))


def project_model_cost(
    *,
    estimated_tokens: int,
    prompt_cost_per_million: float | None,
    completion_cost_per_million: float | None,
) -> dict[str, float]:
    """Project prompt and completion dollar cost for a token estimate from model
    pricing.

    Multiplies ``estimated_tokens / 1_000_000`` by each per-million price; a ``None``
    price counts as zero. Returns ``{"projected_prompt_cost",
    "projected_completion_cost"}`` rounded to 8 decimals. Raises ``ValueError`` when
    ``estimated_tokens`` is negative.
    """
    if estimated_tokens < 0:
        raise ValueError("estimated_tokens must be >= 0")

    units = estimated_tokens / 1_000_000
    prompt_cost = float(prompt_cost_per_million or 0.0)
    completion_cost = float(completion_cost_per_million or 0.0)
    return {
        "projected_prompt_cost": round(prompt_cost * units, 8),
        "projected_completion_cost": round(completion_cost * units, 8),
    }
