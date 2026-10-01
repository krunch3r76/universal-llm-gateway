"""Parallel Auto admits for ``isolated_lane_conductor`` (G5 F9–F16, F7)."""

from __future__ import annotations


def test_f5_allowlisted_contracts_not_propagate_in_seat_only() -> None:
    from libs.contract_vocab.records import nested_scope_contracts

    for c in ("investigate", "recon", "verify"):
        assert c in nested_scope_contracts()














