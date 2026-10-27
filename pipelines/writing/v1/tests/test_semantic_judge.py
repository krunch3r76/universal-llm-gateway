"""Semantic judge handler tests (stubbed model, offline)."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

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

semantic_judge = importlib.import_module("writing_v1_handlers.semantic_judge")
judge_events = importlib.import_module("writing_pipeline.events")

pytestmark = pytest.mark.offline


class _Out:
    def __init__(self, payload: dict[str, Any] | None, *, raw: str = "") -> None:
        self.json = payload
        self.error = None
        self.raw = raw


class _Ctx:
    def __init__(self, outputs: dict[str, Any]) -> None:
        self.outputs = outputs


class _Step:
    def __init__(self, invocation: str = "candidates") -> None:
        self._invocation = invocation

    def get_domain_field(self, key: str) -> Any:
        if key == "invocation":
            return self._invocation
        return None


def _pin_payload(candidate_id: str) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "candidate_id": candidate_id,
                "pins": [
                    {
                        "pin_id": "P1",
                        "satisfied": True,
                        "polarity_match": True,
                        "evidence_span": "backed",
                    }
                ],
                "clauses": [
                    {
                        "text": "Backed.",
                        "pin_ids": ["P1"],
                        "speech_act": "assertion",
                        "attested": True,
                    }
                ],
            }
        ]
    }


def _quality_payload(candidate_id: str) -> dict[str, Any]:
    return {
        "findings": [
            {
                "candidate_id": candidate_id,
                "rule": "register",
                "detail": "ok",
                "penalty": 0,
            }
        ]
    }


@pytest.mark.asyncio
async def test_semantic_judge_two_calls_qwen_alias() -> None:
    calls: list[str] = []

    async def caller(**kwargs: Any) -> str:
        which = kwargs.get("which")
        calls.append(str(which))
        if which == "pin":
            return json.dumps(_pin_payload("drafter_anthropic"))
        return json.dumps(_quality_payload("drafter_anthropic"))

    ctx = _Ctx(
        {
            "assemble": _Out(
                {
                    "pin_ledger": [{"pin_id": "P1", "attested": True, "excerpt": "x"}],
                    "intake": {},
                }
            ),
            "drafter_anthropic": _Out(None, raw="Backed."),
        }
    )
    out = await semantic_judge.WritingSemanticJudgeHandler(caller=caller).execute(
        _Step("candidates"), ctx
    )
    assert calls == ["pin", "quality"]
    assert out.json["ok"] is True
    assert out.model_id == "qwen3-5-9b"


@pytest.mark.asyncio
async def test_semantic_judge_schema_invalid_unavailable() -> None:
    async def caller(**kwargs: Any) -> str:
        if kwargs.get("which") == "pin":
            return json.dumps({"candidates": [{"candidate_id": "drafter_anthropic"}]})
        return json.dumps(_quality_payload("drafter_anthropic"))

    ctx = _Ctx(
        {
            "assemble": _Out({"pin_ledger": [], "intake": {}}),
            "drafter_anthropic": _Out(None, raw="Backed."),
        }
    )
    out = await semantic_judge.WritingSemanticJudgeHandler(caller=caller).execute(
        _Step("candidates"), ctx
    )
    assert out.json["judge_unavailable"] is True


def test_judge_event_signals() -> None:
    completed = judge_events.WritingJudgeCompleted(
        invocation="candidates", model_id="qwen3-5-9b"
    )
    failed = judge_events.WritingJudgeFailed(invocation="v2", reason="transport")
    assert completed.signal == "writing.judge.completed"
    assert failed.signal == "writing.judge.failed"
