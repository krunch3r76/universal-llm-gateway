"""Shared token-budget estimation helpers for pipeline tooling."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import TypedDict


class PackedBatch(TypedDict):
    """Packed batch result with item names and token total."""

    items: list[str]
    tokens: int


@dataclass(slots=True, kw_only=True)
class EstimateItem:
    """Single item to estimate and batch."""

    name: str
    chars: int
    tokens: int


def estimate_tokens(chars: int, *, chars_per_token: float) -> int:
    """Estimate token count from character count."""
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
    """Pack items into budgeted batches using first-fit decreasing."""
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
    """Estimate validate-step token envelope for code-review pipeline."""
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
    """Project prompt/completion cost from optional model pricing."""
    if estimated_tokens < 0:
        raise ValueError("estimated_tokens must be >= 0")

    units = estimated_tokens / 1_000_000
    prompt_cost = float(prompt_cost_per_million or 0.0)
    completion_cost = float(completion_cost_per_million or 0.0)
    return {
        "projected_prompt_cost": round(prompt_cost * units, 8),
        "projected_completion_cost": round(completion_cost * units, 8),
    }
