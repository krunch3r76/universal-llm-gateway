"""Canonical agent-bus request-contract vocabulary.

Single edit to ``RECORDS`` admits a new wire contract into derived sets.
Fail-loud intake (``normalize_wire_contract``) stays in mcp-server.
"""

from __future__ import annotations

from contract_vocab.records import (
    CANONICAL_CONTRACTS,
    DEFAULT_CONTRACT,
    DEPRECATED_ALIASES,
    RECORDS,
    ContractRecord,
    closeout_table,
    code_work_contracts,
    nested_scope_contracts,
    vision_required_admit_disclosure,
    vision_required_contracts,
    vocab_line,
)

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('git_integration_worker', 'mcp')

__all__ = [
    "CANONICAL_CONTRACTS",
    "DEFAULT_CONTRACT",
    "DEPRECATED_ALIASES",
    "RECORDS",
    "ContractRecord",
    "closeout_table",
    "code_work_contracts",
    "nested_scope_contracts",
    "vocab_line",
    "vision_required_admit_disclosure",
    "vision_required_contracts",
]
