"""Monotonic hop_seq resolution (Shape A AC6)."""

from __future__ import annotations

import pytest

from .conductor_hop_seq import resolve_next_hop_seq


@pytest.mark.offline
def test_monotonic_from_ledger_max() -> None:
    assert (
        resolve_next_hop_seq(
            ledger_max_hop_seq=15,
            prior_hop_seq=None,
            closeout_hop_seq=None,
        )
        == 16
    )


@pytest.mark.offline
def test_unresolved_when_all_missing() -> None:
    assert (
        resolve_next_hop_seq(
            ledger_max_hop_seq=None,
            prior_hop_seq=None,
            closeout_hop_seq=None,
        )
        is None
    )


@pytest.mark.offline
def test_summoning_house_not_applied() -> None:
    from pathlib import Path

    body_path = Path(__file__).with_name("conductor_hop_body.py")
    text = body_path.read_text(encoding="utf-8")
    assert "_summoning_house_model_fields" not in text
