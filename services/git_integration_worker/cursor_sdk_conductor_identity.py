"""Post-admit conductor row identity — SQL ``contract`` column sole source."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def is_conductor_dispatch_row(row: Mapping[str, Any]) -> bool:
    """Return True when the persisted row's SQL contract is conductor."""
    contract = row.get("contract") if hasattr(row, "get") else None
    if contract is None and hasattr(row, "keys") and "contract" in row.keys():
        contract = row["contract"]  # type: ignore[index]
    return str(contract or "").lower() == "conductor"


_DENSITY_HARNESS_STEER_CONTRACTS = frozenset({"conductor", "investigate", "confer"})


def is_density_harness_steer_row(row: Mapping[str, Any]) -> bool:
    """True when the row may receive a one-shot density-harness wrap-up steer."""
    contract = row.get("contract") if hasattr(row, "get") else None
    if contract is None and hasattr(row, "keys") and "contract" in row.keys():
        contract = row["contract"]  # type: ignore[index]
    return str(contract or "").lower() in _DENSITY_HARNESS_STEER_CONTRACTS
