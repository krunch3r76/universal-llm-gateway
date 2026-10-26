"""Handlers must not pass arbitrary max_tokens caps to frontier dispatch."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
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

prompt_architect = importlib.import_module("writing_v1_handlers.prompt_architect")
review_local = importlib.import_module("writing_v1_handlers.review_local")

pytestmark = pytest.mark.offline


class _Out:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.json = payload


class _Ctx:
    def __init__(self, outputs: dict[str, _Out]) -> None:
        self.outputs = outputs


def test_prompt_architect_omits_max_tokens_on_call_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []

    async def _fake_call_model(
        _self: object,
        _model_id: str,
        _prompt: str,
        _step: object,
        _context: object,
        **kwargs: Any,
    ) -> object:
        captured.append(kwargs)
        return type("Result", (), {"content": "lead_pin_id: P1"})()

    monkeypatch.setattr(
        prompt_architect.WritingPromptArchitectHandler,
        "_call_model",
        _fake_call_model,
    )
    ctx = _Ctx(
        {
            "assemble": _Out(
                {
                    "refused": None,
                    "intake": {"channel": "email"},
                    "pin_ledger": [{"excerpt": "P1 status"}],
                    "verbatim_notes_block": "",
                    "brief_block": "",
                    "documents_block": "",
                }
            )
        }
    )
    asyncio.run(prompt_architect.WritingPromptArchitectHandler().execute(None, ctx))
    assert captured, "expected _call_model to run"
    assert "max_tokens" not in captured[0]


def test_review_local_omits_max_tokens_on_call_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []

    async def _fake_call_model(
        _self: object,
        _model_id: str,
        _prompt: str,
        _step: object,
        _context: object,
        **kwargs: Any,
    ) -> object:
        captured.append(kwargs)
        return type(
            "Result",
            (),
            {
                "content": (
                    '{"findings": [], "verdict": "ship", "clauses": [{"text": "x"}]}'
                )
            },
        )()

    monkeypatch.setattr(
        review_local.WritingReviewLocalHandler,
        "_call_model",
        _fake_call_model,
    )
    qwen = "qwen3-5-9b-q8-0-131072"
    ctx = _Ctx(
        {
            "assemble": _Out({"documents_block": "", "brief_block": ""}),
            "draft": _Out({"draft": "Backed.", "model_id": qwen}),
            "independence": _Out(
                {
                    "writer_model": qwen,
                    "reviewer_model": "openai/gpt-5.4-mini",
                }
            ),
        }
    )
    asyncio.run(review_local.WritingReviewLocalHandler().execute(None, ctx))
    assert captured, "expected _call_model to run"
    assert "max_tokens" not in captured[0]
    assert captured[0].get("json_schema") is not None
