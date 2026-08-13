"""Canonical wire-contract vocabulary for ``agent_bus.request`` intake.

Intake is the source of truth for the contract vocabulary: an unknown wire
contract fails loud here, before any turn is written, so a bad contract can
never produce a success-shaped bus turn. Worker-side
``resolve_contract_disposition`` keeps its permissive fallback only for
DIRECTIVE *body* overrides, which intake does not parse.

Phase 1 of the ``consult`` retirement (Fable §6): the legacy token is aliased
to ``confer`` and the response carries a deprecation note, so remaining
authors are enumerable from the counter instead of guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contract_vocab import (
    CANONICAL_CONTRACTS,
    DEFAULT_CONTRACT,
    vocab_line,
)
from contract_vocab import (
    DEPRECATED_ALIASES as DEPRECATED_CONTRACT_ALIASES,
)


@dataclass(frozen=True, slots=True)
class ContractIntake:
    """Outcome of normalizing one wire ``contract`` value."""

    contract: str
    requested: str
    alias_of: str | None = None
    error: dict[str, Any] | None = None

    @property
    def deprecated(self) -> bool:
        return self.alias_of is not None

    @property
    def deprecation_note(self) -> str | None:
        if self.alias_of is None:
            return None
        return (
            f"contract {self.alias_of!r} is deprecated; "
            f"use {self.contract!r} (honored this call)"
        )


def _valid_set_line() -> str:
    return vocab_line()


def normalize_wire_contract(contract: str | None) -> ContractIntake:
    """Normalize + validate a wire ``contract``; unknown values carry an error."""
    raw = (contract or "").strip().lower()
    if not raw:
        return ContractIntake(contract=DEFAULT_CONTRACT, requested=DEFAULT_CONTRACT)
    aliased = DEPRECATED_CONTRACT_ALIASES.get(raw)
    if aliased is not None:
        return ContractIntake(contract=aliased, requested=raw, alias_of=raw)
    if raw in CANONICAL_CONTRACTS:
        return ContractIntake(contract=raw, requested=raw)
    return ContractIntake(
        contract=raw,
        requested=raw,
        error={
            "error": (
                f"request: unknown contract={raw!r}; "
                f"valid contracts: {_valid_set_line()}"
            ),
            "reason": "request_contract_unknown",
            "status_code": 422,
            "provided": raw,
            "valid_contracts": list(CANONICAL_CONTRACTS),
            "alias_map": dict(DEPRECATED_CONTRACT_ALIASES),
        },
    )


__all__ = [
    "CANONICAL_CONTRACTS",
    "DEFAULT_CONTRACT",
    "DEPRECATED_CONTRACT_ALIASES",
    "ContractIntake",
    "normalize_wire_contract",
]
