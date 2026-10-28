"""Prose-rhythm reviser handler tests (offline)."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

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

prose_rhythm = importlib.import_module("writing_v1_handlers.prose_rhythm")

pytestmark = pytest.mark.offline

_FOUR_FINDINGS = [
    {"ref": "s1", "type": "omission", "fix": "Add the requested deadline."},
    {"ref": "s2", "type": "unsupported", "fix": "Remove the unsupported vendor name."},
    {"ref": "s3", "type": "tone", "fix": "Use a warmer greeting."},
    {"ref": "s4", "type": "structure", "fix": "Split the long second paragraph."},
]

_DRAFT_V1 = (
    "To: client@example.com\nSubject: Re: timing\n\n"
    "Hello. One long paragraph that should be split for readability.\n\n"
    "Best,\nPat"
)


class _Out:
    def __init__(
        self,
        payload: dict[str, Any] | None,
        *,
        raw: str = "",
    ) -> None:
        self.json = payload
        self.error = None
        self.raw = raw


class _Ctx:
    def __init__(self, outputs: dict[str, Any]) -> None:
        self.outputs = outputs


class _Step:
    handler_inputs = {
        "draft": "draft.json.draft",
        "documents": "assemble.json.documents_block",
        "brief": "assemble.json.brief_block",
        "missing_obligations": "obligation_coverage.json.missing_obligations_block",
        "source_obligations": "source_obligations.json.source_obligations_block",
    }
    generation_parameters = {"temperature": 0.3}


def test_prose_rhythm_applies_findings_when_model_unchanged() -> None:
    caller = AsyncMock(return_value=_DRAFT_V1)
    handler = prose_rhythm.WritingProseRhythmHandler(caller=caller)
    outputs = {
        "assemble": _Out({"documents_block": "", "brief_block": ""}),
        "draft": _Out({"draft": _DRAFT_V1}),
        "obligation_coverage": _Out({"missing_obligations_block": ""}),
        "source_obligations": _Out({"source_obligations_block": ""}),
        "fact_ceiling_audit": _Out(
            {"verdict": "revise", "findings": _FOUR_FINDINGS, "clauses": []}
        ),
    }
    out = asyncio.run(handler.execute(_Step(), _Ctx(outputs)))
    assert out.raw.strip() != _DRAFT_V1.strip()
    assert out.json is not None
    assert len(out.json["dispositions"]) == 4
    refs = {row["ref"] for row in out.json["dispositions"]}
    assert refs == {"s1", "s2", "s3", "s4"}
    for row in out.json["dispositions"]:
        assert row["status"] in {"applied", "declined"}
        assert row.get("reason") is not None or row["status"] == "applied"


def test_revision_dispositions_names_each_finding() -> None:
    draft_v2 = _DRAFT_V1 + "\n\nAdd the requested deadline."
    rows = prose_rhythm.revision_dispositions(_DRAFT_V1, draft_v2, _FOUR_FINDINGS)
    assert len(rows) == 4
    assert all(row["ref"] in {"s1", "s2", "s3", "s4"} for row in rows)
