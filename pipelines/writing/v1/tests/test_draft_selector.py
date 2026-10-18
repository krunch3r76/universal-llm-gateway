"""Draft selector scoring tests (offline)."""

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

draft_selector = importlib.import_module("writing_v1_handlers.draft_selector")

pytestmark = pytest.mark.offline


def test_score_draft_requires_full_pin_anchor_coverage() -> None:
    attested = [
        {"pin_id": "P1", "attested": True, "excerpt": "Fill was 251 when I opened Saturday"},
        {"pin_id": "P2", "attested": True, "excerpt": "It was 213 Sunday morning"},
    ]
    weak = "It is what it was."
    strong = (
        "Fill was 251 when I opened Saturday. It was 213 Sunday morning."
    )
    weak_score = draft_selector._score_draft(weak, attested)
    strong_score = draft_selector._score_draft(strong, attested)
    assert weak_score < strong_score
    assert weak_score == 0
    assert strong_score == 2
