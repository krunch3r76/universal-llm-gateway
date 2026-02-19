"""
Cross-model mapping utilities with optional origin exclusion.

These utilities expand items across models, optionally excluding
self-originated pairs to enforce independence.

Design:
- Provenance tracking is MANDATORY (schema-level)
- Origin exclusion is OPTIONAL (policy-controlled via exclude_origin parameter)
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from .assertions import extract_provenance


def _get_originator(
    item: dict[str, Any],
    provenance_field: str,
) -> str | None:
    """
    Extract originator_model_id from item.

    Args:
        item: Artifact that may have provenance
        provenance_field: Field name containing Provenance

    Returns:
        originator_model_id or None if provenance missing/malformed

    Raises:
        KeyError: If provenance dict is malformed (missing required keys)
    """
    try:
        prov = extract_provenance(item, provenance_field)
        return prov.originator_model_id if prov else None
    except KeyError:
        # Malformed provenance dict - re-raise with context
        stmt_id = item.get("statement_id", str(id(item)))
        raise KeyError(f"Malformed provenance in item '{stmt_id}'") from None


def validate_provenance_present(
    items: Iterable[dict[str, Any]],
    *,
    provenance_field: str = "provenance",
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Validate all items have provenance.

    Args:
        items: Artifacts that should have provenance
        provenance_field: Field name containing Provenance

    Returns:
        (valid_items, error_messages) where error_messages lists missing/malformed provenance
    """
    valid = []
    errors = []
    for item in items:
        stmt_id = item.get("statement_id", str(id(item)))
        try:
            prov = extract_provenance(item, provenance_field)
            if prov is None:
                errors.append(f"Missing provenance for item '{stmt_id}'")
            else:
                valid.append(item)
        except KeyError as e:
            errors.append(f"Malformed provenance for item '{stmt_id}': {e}")
    return valid, errors


def expand_models(
    items: Iterable[dict[str, Any]],
    models: Iterable[str],
    *,
    exclude_origin: bool = False,
    provenance_field: str = "provenance",
) -> Iterator[tuple[dict[str, Any], str]]:
    """
    Expand items × models, optionally excluding self-originated pairs.

    Args:
        items: Artifacts with provenance
        models: Candidate models
        exclude_origin: If True, skip (item, model) where item.originator == model
        provenance_field: Field name containing Provenance

    Yields:
        (item, model) pairs; with exclude_origin=True, item.originator != model

    Raises:
        KeyError: If exclude_origin=True and any item has malformed provenance
    """
    models_list = list(models)
    for item in items:
        originator = _get_originator(item, provenance_field) if exclude_origin else None
        for model in models_list:
            if exclude_origin and originator == model:
                continue
            yield (item, model)


def group_by_eligible_models(
    items: Iterable[dict[str, Any]],
    models: Iterable[str],
    *,
    exclude_origin: bool = False,
    provenance_field: str = "provenance",
) -> dict[str, list[dict[str, Any]]]:
    """
    Group items by which models can process them.

    Args:
        items: Artifacts with provenance
        models: Candidate models
        exclude_origin: If True, exclude items from their originator
        provenance_field: Field name containing Provenance

    Returns:
        {model_id: [items eligible for this model]}

    Raises:
        KeyError: If exclude_origin=True and any item has malformed provenance
    """
    models_list = list(models)
    items_list = list(items)

    result: dict[str, list[dict[str, Any]]] = {m: [] for m in models_list}

    for item in items_list:
        originator = _get_originator(item, provenance_field) if exclude_origin else None
        for model in models_list:
            if exclude_origin and originator == model:
                continue
            result[model].append(item)

    return result


def order_models_by_affinity(
    models: list[str],
    priority_models: set[str],
) -> list[str]:
    """Order models for sequential dispatch: priority models first, remainder last.

    Maximizes request locality — grouping requests for likely-loaded models
    before models that may require loading.  Preserves relative order within
    each partition so callers control intra-group ordering.

    Args:
        models: Model identifiers to reorder (aliases or resolved IDs).
        priority_models: Models expected to be loaded (e.g. answer pool).
            Models in this set are dispatched first.

    Returns:
        Reordered list: [priority ∩ models] ++ [models ∖ priority]
    """
    priority = [m for m in models if m in priority_models]
    deferred = [m for m in models if m not in priority_models]
    return priority + deferred


def count_eligible_verifiers(
    item: dict[str, Any],
    models: Iterable[str],
    *,
    exclude_origin: bool = False,
    provenance_field: str = "provenance",
) -> int:
    """
    Count how many models can verify this item.

    Useful for detecting items that have insufficient verifier coverage.

    Args:
        item: Artifact with provenance
        models: Candidate models
        exclude_origin: If True, exclude the originator
        provenance_field: Field name containing Provenance

    Returns:
        Number of eligible models

    Raises:
        KeyError: If exclude_origin=True and item has malformed provenance
    """
    models_list = list(models)
    if not exclude_origin:
        return len(models_list)

    originator = _get_originator(item, provenance_field)
    return sum(1 for m in models_list if m != originator)
