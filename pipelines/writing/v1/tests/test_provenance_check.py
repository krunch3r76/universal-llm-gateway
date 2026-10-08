"""Provenance-check tests for writer-specialist v1.

The pure checker is the subject. No client, network, or model is constructed.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

_PKG = "writing_v1_handlers"
if _PKG not in sys.modules:
    _HANDLERS = Path(__file__).resolve().parents[1] / "handlers"
    _spec = importlib.util.spec_from_file_location(
        _PKG,
        _HANDLERS / "__init__.py",
        submodule_search_locations=[str(_HANDLERS)],
    )
    assert _spec is not None and _spec.loader is not None
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_PKG] = _mod
    _spec.loader.exec_module(_mod)

provenance = importlib.import_module("writing_v1_handlers.provenance_check")

pytestmark = pytest.mark.offline

_LEDGER = [
    {"pin_id": "P1", "attested": True, "excerpt": "backed"},
    {"pin_id": "P2", "attested": False, "excerpt": "rumor"},
]


def _draft(text: str, claims: list[dict]) -> dict:
    return {"draft": text, "claims": claims, "need": [], "dispositions": []}


def _types(violations: list[dict]) -> list[str]:
    return [row["type"] for row in violations]


def test_provenance_unknown_pin() -> None:
    violations = provenance.check_provenance(
        _LEDGER,
        _draft(
            "Said it.",
            [
                {
                    "claim_id": "c1",
                    "text": "Said it.",
                    "pin_ids": ["P9"],
                    "disposition": "express",
                    "state": "backed",
                }
            ],
        ),
    )
    assert "unknown_pin" in _types(violations)
    row = next(item for item in violations if item["type"] == "unknown_pin")
    assert row["claim_id"] == "c1"
    assert row["detail"] == "P9"


def test_provenance_unattested_fact() -> None:
    violations = provenance.check_provenance(
        _LEDGER,
        _draft(
            "The rumor is fact.",
            [
                {
                    "claim_id": "c2",
                    "text": "The rumor is fact.",
                    "pin_ids": ["P2"],
                    "disposition": "express",
                    "state": "backed",
                }
            ],
        ),
    )
    assert "unattested_pin" in _types(violations)
    assert "unknown_pin" not in _types(violations)


def test_provenance_pinless_claim_needs_marker() -> None:
    marked = provenance.check_provenance(
        _LEDGER,
        _draft(
            "Color is [NEED: wavelength].",
            [
                {
                    "claim_id": "c3",
                    "text": "Color is [NEED: wavelength].",
                    "pin_ids": [],
                    "disposition": "express",
                    "state": "need",
                }
            ],
        ),
    )
    unverified = provenance.check_provenance(
        _LEDGER,
        _draft(
            "Maybe [unverified: claim].",
            [
                {
                    "claim_id": "c4",
                    "text": "Maybe [unverified: claim].",
                    "pin_ids": [],
                    "disposition": "imply",
                    "state": "unverified",
                }
            ],
        ),
    )
    unmarked = provenance.check_provenance(
        _LEDGER,
        _draft(
            "The sky is green.",
            [
                {
                    "claim_id": "c5",
                    "text": "The sky is green.",
                    "pin_ids": [],
                    "disposition": "express",
                    "state": "backed",
                }
            ],
        ),
    )
    assert "pinless_claim_unmarked" not in _types(marked)
    assert "pinless_claim_unmarked" not in _types(unverified)
    assert "pinless_claim_unmarked" in _types(unmarked)


def test_provenance_em_dash() -> None:
    violations = provenance.check_provenance(
        _LEDGER,
        _draft(
            "Left \u2014 right \u2014 end.",
            [
                {
                    "claim_id": "c1",
                    "text": "Left.",
                    "pin_ids": ["P1"],
                    "disposition": "express",
                    "state": "backed",
                }
            ],
        ),
    )
    rows = [row for row in violations if row["type"] == "em_dash"]
    assert len(rows) == 1
    assert rows[0]["claim_id"] is None
    assert rows[0]["detail"] == 2


def test_provenance_persona_line() -> None:
    violations = provenance.check_provenance(
        _LEDGER,
        _draft(
            "I am an AI helper.\nBacked.",
            [
                {
                    "claim_id": "c1",
                    "text": "Backed.",
                    "pin_ids": ["P1"],
                    "disposition": "express",
                    "state": "backed",
                }
            ],
        ),
    )
    assert "persona_line" in _types(violations)


def test_provenance_bolted_closer() -> None:
    violations = provenance.check_provenance(
        _LEDGER,
        _draft(
            "Backed sentence.\n\nIn summary, that is all.",
            [
                {
                    "claim_id": "c1",
                    "text": "Backed sentence.",
                    "pin_ids": ["P1"],
                    "disposition": "express",
                    "state": "backed",
                }
            ],
        ),
    )
    assert "bolted_closer" in _types(violations)


def test_provenance_draft_unparseable() -> None:
    for bad in (None, "not-json", {"claims": []}, ["nope"]):
        violations = provenance.check_provenance(_LEDGER, bad)
        assert _types(violations) == ["draft_unparseable"]
        assert violations[0]["claim_id"] is None
