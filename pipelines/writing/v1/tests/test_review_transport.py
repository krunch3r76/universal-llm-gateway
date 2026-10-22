"""Local review transport failure stays inside the pipeline package.

The DAG executor cancels the run when a step raises. This test drives the
review handler, ConditionEvaluator, select, and finalize. It does not start
the executor.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
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
assemble = importlib.import_module("writing_v1_handlers.assemble")
seat_dispatch = importlib.import_module("writing_v1_handlers.seat_dispatch")

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


async def _boom(**_kwargs: object) -> str:
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
    assert selected.json["refused"] == "review_transport_failed"
    assert selected.json["reason"] == "review_transport_failed"
    outputs = {**base, "review_local": local, "review": selected}
    evaluator = ConditionEvaluator()

    def runs(name: str) -> bool:
        wrapped = {
            key: value if isinstance(value, StepOutput) else value
            for key, value in outputs.items()
        }
        return evaluator.evaluate(by_name[name].condition, wrapped, {})

    if "prose_rhythm_pass" in by_name:
        assert runs("prose_rhythm_pass") is False
    if "fact_ceiling_final" in by_name:
        assert runs("fact_ceiling_final") is False
    envelope = asyncio.run(
        finalize.WritingFinalizeHandler().execute(None, _Ctx(outputs))
    ).json
    assert envelope["draft_v1"]["draft"] == "Backed."
    assert envelope["review"]["status"] == "failed"
    assert envelope["review"]["reason"] == "review_transport_failed"
    assert envelope["provenance"]["initial"] == []
    assert envelope["revision"] is None
    assert envelope["ship_gate"]["pass"] is False
    assert any(
        row["type"] == "review_unresolved"
        and row["detail"] == "review_transport_failed"
        for row in envelope["ship_gate"]["unresolved"]
    )


def test_review_local_skips_same_family_transport_fallback(monkeypatch) -> None:
    calls: list[str] = []

    async def _fake_call_model(
        _self: object,
        model_id: str,
        *_args: object,
        **_kwargs: object,
    ) -> object:
        calls.append(model_id)
        if model_id == "openai/gpt-5.4-mini":
            raise httpx.ConnectError("simulated")
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
    base = {
        "assemble": _Out({"documents_block": "", "brief_block": "", "refused": None}),
        "draft": _Out({"draft": "Backed.", "model_id": qwen}),
        "independence": _Out(
            {
                "refused": None,
                "independence": "full",
                "writer_model": qwen,
                "reviewer_model": "openai/gpt-5.4-mini",
            }
        ),
    }
    parsed = asyncio.run(
        review_local.WritingReviewLocalHandler().execute(
            _Step("fact_ceiling_audit"), _Ctx(base)
        )
    ).json
    assert parsed["reviewer_model"] == "xai/grok-4-fast-non-reasoning"
    assert "deepseek-r1-distill-qwen-14b-q8-0-65536" not in calls
    assert calls[0] == "openai/gpt-5.4-mini"
    assert calls[-1] == "xai/grok-4-fast-non-reasoning"

    outputs = {
        **base,
        "fact_ceiling_audit": _Out(parsed),
        "provenance_check": _Out({"violations": [], "pass": True, "drift_check": {}}),
    }
    envelope = asyncio.run(
        finalize.WritingFinalizeHandler().execute(None, _Ctx(outputs))
    ).json
    assert envelope["review"]["independence"] == "full"
    assert envelope["seats_used"]["reviewer"] == "xai/grok-4-fast-non-reasoning"


def test_review_local_call_receives_schema() -> None:
    seen: dict = {}

    async def _caller(**kwargs: object) -> str:
        seen.update(kwargs)
        return '{"findings": [], "verdict": "ship"}'

    base = {
        "assemble": _Out({"documents_block": "", "brief_block": "", "refused": None}),
        "draft": _Out({"draft": "Backed."}),
    }
    parsed = asyncio.run(
        review_local.WritingReviewLocalHandler(caller=_caller).execute(
            _Step("review_local"), _Ctx(base)
        )
    ).json
    assert parsed["verdict"] == "ship"
    assert parsed["clauses"] == []
    schema = seen["json_schema"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["findings", "verdict", "clauses"]
    assert (
        "omission"
        in schema["properties"]["findings"]["items"]["properties"]["type"]["enum"]
    )


def test_sensitive_brief_runs_through_finalize() -> None:
    marker = "PINMARKER-16001-E2E"

    class _Brief:
        def __init__(self) -> None:
            self.options = {
                "brief": {
                    "signer": "Ada",
                    "objective": marker,
                    "recipient": "[NEED: recipient]",
                    "deliverable": {"genre": "memo"},
                    "material_attested": ["status"],
                    "not_attested": [],
                    "sensitivity": "sensitive",
                },
                "working_set": [],
                "writer_seat": "local",
                "reviewer_seat": "cdp",
                "output": "envelope",
            }
            self.outputs = {}

    assembled = asyncio.run(assemble.WritingAssembleHandler().execute(None, _Brief()))
    assert assembled.json.get("refused") is None
    assert assembled.json["sensitivity"] == "sensitive"
    assert marker in assembled.json["brief_block"]

    class _Post:
        def __init__(self) -> None:
            self.posts: list[dict] = []

        async def post(self, path: str, json: dict | None = None) -> object:
            self.posts.append(json or {})

            class _Resp:
                status_code = 200

                def json(self) -> dict:
                    return {"execution_id": "e-sens", "poll_hint": {"arguments": {}}}

            return _Resp()

    client = _Post()
    seat_dispatch._POSTED.clear()
    dispatched = asyncio.run(
        seat_dispatch.WritingSeatDispatchHandler(
            client=client,
            sidecar_writer=lambda _prompt, _key: "cortex://notes/off-bus.txt",
        ).execute(
            _Step("review_dispatch"),
            type("C", (), {"outputs": {"assemble": assembled}, "options": {}})(),
        )
    )
    assert dispatched.json.get("refused") is None
    assert marker not in json.dumps(client.posts)
    draft = {
        "draft": "Please send the date.",
        "claims": [],
        "need": [],
        "dispositions": [],
    }
    review = {"findings": [], "verdict": "ship", "clauses": []}
    outputs = {
        "assemble": assembled,
        "draft": _Out(draft),
        "provenance_check": _Out({"violations": [], "pass": True}),
        "independence": _Out({"refused": None, "independence": "full"}),
        "review_dispatch": dispatched,
        "review": _Out(review),
    }
    envelope = asyncio.run(
        finalize.WritingFinalizeHandler().execute(None, _Ctx(outputs))
    ).json
    assert envelope.get("refused") is None
    assert envelope["draft_v1"]["draft"] == "Please send the date."
    assert envelope["unsent"] is True
