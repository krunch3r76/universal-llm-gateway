"""
Provenance tracking for pipeline artifacts.

This module provides:
- Provenance: Immutable record tracking content authorship and processing
- Independence assertions: Verify evaluator != originator
- Cross-model utilities: Expand items × models with optional exclusion

Design principles:
1. Provenance is schema-level and mandatory for aggregation steps
2. Independence (originator exclusion) is optional and policy-controlled
3. Lineage tracks all processors but doesn't affect independence checks

Example:
    from provenance import Provenance, create_provenance, is_independent

    # Create provenance when model generates content
    prov = create_provenance(model_id="llama", step_id="answer")

    # Extend when another step processes it
    prov = prov.with_processor(step_id="decompose", processor_model_id="phi")

    # Check independence
    if is_independent(prov, evaluator_model_id="llama"):
        # LLaMA can verify this (originator != evaluator)
        pass
"""

from .assertions import (
    IndependenceViolationError,
    assert_independent,
    assert_provenance_present,
    extract_provenance,
    is_independent,
)
from .cross_model import (
    count_eligible_verifiers,
    expand_models,
    group_by_eligible_models,
    validate_provenance_present,
)
from .types import Provenance, create_provenance

__all__ = [
    # Types
    "Provenance",
    "create_provenance",
    # Assertions
    "IndependenceViolationError",
    "is_independent",
    "assert_independent",
    "extract_provenance",
    "assert_provenance_present",
    # Cross-model utilities
    "expand_models",
    "group_by_eligible_models",
    "count_eligible_verifiers",
    "validate_provenance_present",
]
