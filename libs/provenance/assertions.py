"""
Independence assertions for provenance-aware operations.

Key insight: Independence is about AUTHORSHIP, not processing history.
Lineage entries are irrelevant to independence checks.
"""

from __future__ import annotations

from typing import Any

from .types import Provenance


class IndependenceViolationError(Exception):
    """Raised when independence invariant is violated."""

    def __init__(
        self,
        provenance: Provenance,
        evaluator_model_id: str,
        context: str = "",
    ) -> None:
        self.provenance = provenance
        self.evaluator_model_id = evaluator_model_id
        msg = (
            f"Independence violation: evaluator '{evaluator_model_id}' "
            f"cannot judge content originated by '{provenance.originator_model_id}'"
        )
        if context:
            msg = f"{msg} [{context}]"
        super().__init__(msg)


def is_independent(provenance: Provenance, evaluator_model_id: str) -> bool:
    """
    Check if evaluator is independent of content originator.

    Compares ONLY originator_model_id — lineage entries are irrelevant.

    Args:
        provenance: The artifact's provenance record
        evaluator_model_id: Model that would evaluate/judge this artifact

    Returns:
        True if evaluator != originator
    """
    return provenance.originator_model_id != evaluator_model_id


def assert_independent(
    provenance: Provenance,
    evaluator_model_id: str,
    context: str = "",
) -> None:
    """
    Assert evaluator is independent of content originator.

    Raises IndependenceViolation if originator == evaluator.

    Args:
        provenance: The artifact's provenance record
        evaluator_model_id: Model that would evaluate/judge this artifact
        context: Optional context for error message

    Raises:
        IndependenceViolationError: If independence invariant violated
    """
    if not is_independent(provenance, evaluator_model_id):
        raise IndependenceViolationError(provenance, evaluator_model_id, context)


def extract_provenance(
    artifact: dict[str, Any],
    field: str = "provenance",
) -> Provenance | None:
    """
    Extract Provenance from artifact dict.

    Handles both embedded Provenance objects and serialized dicts.

    Args:
        artifact: Dict containing provenance data
        field: Field name where provenance is stored

    Returns:
        Provenance object or None if not present
    """
    data = artifact.get(field)
    if data is None:
        return None
    if isinstance(data, Provenance):
        return data
    if isinstance(data, dict):
        return Provenance.from_dict(data)
    return None


def assert_provenance_present(
    artifact: dict[str, Any],
    field: str = "provenance",
    context: str = "",
) -> Provenance:
    """
    Assert artifact has provenance and return it.

    Args:
        artifact: Dict that should contain provenance
        field: Field name where provenance is stored
        context: Optional context for error message

    Returns:
        The extracted Provenance

    Raises:
        ValueError: If provenance is missing or malformed
    """
    prov = extract_provenance(artifact, field)
    if prov is None:
        msg = f"Missing provenance in field '{field}'"
        if context:
            msg = f"{msg} [{context}]"
        raise ValueError(msg)
    return prov
