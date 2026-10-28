"""Intake-answer guard on writing_review_local_v1 (offline)."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
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

review_local = importlib.import_module("writing_v1_handlers.review_local")
review_schema = importlib.import_module("writing_v1_handlers.review_schema")
seat_dispatch = importlib.import_module("writing_v1_handlers.seat_dispatch")

pytestmark = pytest.mark.offline

_DRAFT = "Line one.\nLine two with answer token.\n"


class _Out:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.json = payload


class _Ctx:
    def __init__(self, outputs: dict[str, Any]) -> None:
        self.outputs = outputs


def _promoted_row(ref: str = "s2") -> dict[str, str]:
    return {
        "ref": ref,
        "type": "intake_answer_promoted",
        "fix": "Remove promoted answer.",
    }


async def _run_review(
    review_json: dict[str, Any],
    *,
    intake_answers: list[str] | None = None,
    draft: str = _DRAFT,
) -> dict[str, Any]:
    caller = AsyncMock(return_value=json.dumps(review_json))
    handler = review_local.WritingReviewLocalHandler(caller=caller)
    assemble = {"documents_block": "", "brief_block": "", "intake": {}}
    if intake_answers is not None:
        assemble["intake"] = {"intake_answers": intake_answers}
    outputs = {
        "assemble": _Out(assemble),
        "draft": _Out({"draft": draft}),
    }
    out = await handler.execute(None, _Ctx(outputs))
    assert out.json is not None
    return out.json


@pytest.mark.asyncio
async def test_guard_suppresses_uncorroborated_intake_promoted() -> None:
    payload = await _run_review(
        {"findings": [_promoted_row()], "verdict": "revise", "clauses": []},
        intake_answers=[],
    )
    assert payload["findings"] == []
    assert payload["verdict"] == "ship"
    assert len(payload["suppressed_findings"]) == 1
    assert payload["suppressed_findings"][0]["reason"] == "intake_answer_uncorroborated"


@pytest.mark.asyncio
async def test_guard_keeps_corroborated_intake_promoted() -> None:
    payload = await _run_review(
        {"findings": [_promoted_row()], "verdict": "revise", "clauses": []},
        intake_answers=["answer token"],
    )
    assert len(payload["findings"]) == 1
    assert payload["verdict"] == "revise"
    assert payload["suppressed_findings"] == []


@pytest.mark.asyncio
async def test_guard_uses_ref_haystack_when_in_draft() -> None:
    sentence = "Line two with answer token."
    payload = await _run_review(
        {
            "findings": [_promoted_row(ref=sentence)],
            "verdict": "revise",
            "clauses": [],
        },
        intake_answers=["elsewhere only"],
        draft=_DRAFT,
    )
    assert payload["findings"] == []
    assert payload["verdict"] == "ship"


@pytest.mark.asyncio
async def test_guard_mixed_findings() -> None:
    payload = await _run_review(
        {
            "findings": [
                _promoted_row(),
                {"ref": "s9", "type": "not_purpose_relevant", "fix": "Cut."},
            ],
            "verdict": "revise",
            "clauses": [],
        },
        intake_answers=[],
    )
    assert len(payload["findings"]) == 1
    assert payload["findings"][0]["type"] == "not_purpose_relevant"
    assert payload["verdict"] == "revise"


@pytest.mark.asyncio
async def test_guard_rowless_revise_stays_revise() -> None:
    payload = await _run_review(
        {"findings": [], "verdict": "revise", "clauses": []},
        intake_answers=[],
    )
    assert payload["verdict"] == "revise"
    assert payload["suppressed_findings"] == []


@pytest.mark.asyncio
async def test_refusal_has_no_suppressed_findings() -> None:
    async def boom(**_kwargs: Any) -> str:
        raise RuntimeError("down")

    handler = review_local.WritingReviewLocalHandler(caller=boom)
    outputs = {
        "assemble": _Out({"documents_block": "", "brief_block": "", "intake": {}}),
        "draft": _Out({"draft": _DRAFT}),
    }
    out = await handler.execute(None, _Ctx(outputs))
    assert out.json is not None
    assert out.json.get("refused") == "review_transport_failed"
    assert "suppressed_findings" not in out.json


def test_guarded_object_fails_schema_additional_properties() -> None:
    sample = {
        "findings": [],
        "verdict": "ship",
        "clauses": [],
        "suppressed_findings": [_promoted_row() | {"reason": "intake_answer_uncorroborated"}],
        "reviewer_model": "qwen",
    }
    assert review_schema.review_result_error(sample) == "additional_properties"
    assert review_schema.REVIEW_RESULT_SCHEMA["additionalProperties"] is False


def test_reviewer_yaml_single_template_and_f1_bullet() -> None:
    prompts = seat_dispatch._prompts()
    assert list(prompts.get("reviewer") or {})  # single reviewer entry in yaml load
    template = str((prompts.get("reviewer") or {}).get("template") or "")
    assert 'provenance="intake_answer"' in template
    assert "never use this type" in template.lower() or "never use this type" in template
    assert "copied exactly from the" in template.lower() or "copied exactly from" in template
