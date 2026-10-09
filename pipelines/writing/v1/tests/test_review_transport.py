"""Local review transport failure stays inside the pipeline package.

The DAG executor cancels the run when a step raises. This test drives the
review handler, ConditionEvaluator, select, and finalize. It does not start
the executor.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import yaml
from systems.pipeline.core.conditions import ConditionEvaluator
from systems.pipeline.core.handlers.step_output import StepOutput
from systems.pipeline.core.pipeline_config import PipelineSpec

_PKG = "writing_v1_handlers"
_V1 = Path(__file__).resolve().parents[1]
if _PKG not in sys.modules:
    _HANDLERS = _V1 / "handlers"
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
seat_wait = importlib.import_module("writing_v1_handlers.seat_wait")
finalize = importlib.import_module("writing_v1_handlers.finalize")

pytestmark = pytest.mark.offline


class _Out:
    def __init__(self, payload: dict) -> None:
        self.json = payload
        self.error = None
        self.model_id = "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid"
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.model_call_count = 0
        self.raw = ""


class _Ctx:
    def __init__(self, outputs: dict) -> None:
        self.outputs = outputs
        self.options = {}


class _Step:
    def __init__(self, name: str) -> None:
        self.name = name


async def _boom() -> str:
    raise httpx.ConnectError("simulated")


def test_review_transport_error_keeps_draft_v1() -> None:
    raw = yaml.safe_load(
        (_V1 / "writer-specialist-v1.yaml").read_text(encoding="utf-8")
    )
    by_name = {step.name: step for step in PipelineSpec(**raw).steps}
    draft = {
        "draft": "Backed.",
        "claims": [],
        "need": [],
        "dispositions": [],
    }
    base = {
        "assemble": _Out(
            {
                "refused": None,
                "writer_seat": "local",
                "reviewer_seat": "local",
                "documents_block": "",
                "brief_block": "",
                "pin_ledger": [],
            }
        ),
        "draft": _Out(draft),
        "provenance_check": _Out({"violations": [], "pass": True}),
        "independence": _Out({"refused": None, "independence": "full"}),
    }
    local = asyncio.run(
        review_local.WritingReviewLocalHandler(caller=_boom).execute(
            _Step("review_local"), _Ctx(base)
        )
    )
    assert local.error is None
    assert local.json["refused"] == "review_transport_failed"
    selected = asyncio.run(
        seat_wait.WritingSeatSelectHandler().execute(
            _Step("review"),
            _Ctx({**base, "review_local": local}),
        )
    )
    assert selected.json["refused"] == "reviewer_seat_failed"
    outputs = {**base, "review_local": local, "review": selected}
    evaluator = ConditionEvaluator()

    def runs(name: str) -> bool:
        wrapped = {
            key: value if isinstance(value, StepOutput) else value
            for key, value in outputs.items()
        }
        return evaluator.evaluate(by_name[name].condition, wrapped, {})

    assert runs("revise") is False
    assert runs("provenance_check_final") is False
    envelope = asyncio.run(
        finalize.WritingFinalizeHandler().execute(None, _Ctx(outputs))
    ).json
    assert envelope["draft_v1"]["draft"] == "Backed."
    assert envelope["review"]["status"] == "failed"
    assert envelope["revision"] is None
    assert envelope["ship_gate"]["pass"] is False
    assert any(
        row["type"] == "review_unresolved"
        for row in envelope["ship_gate"]["unresolved"]
    )
