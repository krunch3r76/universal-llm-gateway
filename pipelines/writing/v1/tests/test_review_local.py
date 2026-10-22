"""Local reviewer schema acceptance for provenance/purpose finding types."""

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

review_schema = importlib.import_module("writing_v1_handlers.review_schema")

pytestmark = pytest.mark.offline


def test_review_schema_accepts_relevance_and_provenance_finding_types() -> None:
    sample = {
        "findings": [
            {
                "ref": "s1",
                "type": "volunteered_negative_or_concession",
                "fix": "Although this is supported by the intake note, remove it.",
            },
            {"ref": "s2", "type": "intake_answer_promoted", "fix": "Drop Q&A residue."},
            {"ref": "s3", "type": "prior_draft_carryover", "fix": "Remove carryover."},
            {"ref": "s4", "type": "not_purpose_relevant", "fix": "Remove courtesy line."},
        ],
        "verdict": "revise",
        "clauses": [{"text": "Body.", "pin_ids": ["P1"], "disposition": "express"}],
    }
    assert review_schema.review_result_error(sample) is None
