"""Semantic judge handler tests (stubbed model, offline)."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

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
    def __init__(
        self,
        payload: dict[str, Any] | None,
        *,
        raw: str = "",
        error: str | None = None,
    ) -> None:
        self.json = payload
        self.error = error
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


def _v2_pin_payload() -> dict[str, Any]:
    return {
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


def _v2_quality_payload(*, empty: bool = False) -> dict[str, Any]:
    if empty:
        return {"findings": []}
    return {
        "findings": [
            {"rule": "register", "detail": "clean", "penalty": 0},
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

    handler = semantic_judge.WritingSemanticJudgeHandler(caller=caller)
    handler._call_model = AsyncMock(  # type: ignore[method-assign]
        side_effect=AssertionError("_call_model must not run when caller is set")
    )
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
    published: list[Any] = []
    handler._publish_bus_event = lambda _ctx, event: published.append(event)  # type: ignore[method-assign]
    out = await handler.execute(_Step("candidates"), ctx)
    assert calls == ["pin", "quality"]
    assert out.json["ok"] is True
    assert out.model_id == "qwen3-5-9b-q8-0-131072"
    assert len(published) == 1
    assert published[0].signal == "writing.judge.completed"
    assert published[0].payload["model_id"] == "qwen3-5-9b-q8-0-131072"


@pytest.mark.asyncio
async def test_semantic_judge_routes_qwen_through_call_model() -> None:
    model_ids: list[str] = []

    async def fake_call_model(model_id: str, *_args: Any, **_kwargs: Any) -> MagicMock:
        model_ids.append(model_id)
        content = (
            json.dumps(_pin_payload("drafter_anthropic"))
            if len(model_ids) == 1
            else json.dumps(_quality_payload("drafter_anthropic"))
        )
        result = MagicMock()
        result.content = content
        return result

    handler = semantic_judge.WritingSemanticJudgeHandler()
    handler._call_model = fake_call_model  # type: ignore[method-assign]
    published: list[Any] = []
    handler._publish_bus_event = lambda _ctx, event: published.append(event)  # type: ignore[method-assign]
    ctx = _Ctx(
        {
            "assemble": _Out({"pin_ledger": [], "intake": {}}),
            "drafter_anthropic": _Out(None, raw="Backed."),
        }
    )
    out = await handler.execute(_Step("candidates"), ctx)
    assert model_ids == ["qwen3-5-9b-q8-0-131072", "qwen3-5-9b-q8-0-131072"]
    assert out.model_id == "qwen3-5-9b-q8-0-131072"
    assert published[0].signal == "writing.judge.completed"


@pytest.mark.asyncio
async def test_semantic_judge_v2_empty_findings_not_unavailable() -> None:
    async def caller(**kwargs: Any) -> str:
        if kwargs.get("which") == "pin":
            return json.dumps(_v2_pin_payload())
        return json.dumps(_v2_quality_payload(empty=True))

    handler = semantic_judge.WritingSemanticJudgeHandler(caller=caller)
    published: list[Any] = []
    handler._publish_bus_event = lambda _ctx, event: published.append(event)  # type: ignore[method-assign]
    ctx = _Ctx(
        {
            "assemble": _Out({"pin_ledger": [], "intake": {}}),
            "prose_rhythm_pass": _Out(None, raw="Revised draft body."),
        }
    )
    out = await handler.execute(_Step("v2"), ctx)
    assert out.json["judge_unavailable"] is False
    assert out.json["quality_findings"] == []
    assert published[0].signal == "writing.judge.completed"


@pytest.mark.asyncio
async def test_semantic_judge_v2_quality_findings_without_candidate_id() -> None:
    async def caller(**kwargs: Any) -> str:
        if kwargs.get("which") == "pin":
            return json.dumps(_v2_pin_payload())
        return json.dumps(_v2_quality_payload())

    handler = semantic_judge.WritingSemanticJudgeHandler(caller=caller)
    ctx = _Ctx(
        {
            "assemble": _Out({"pin_ledger": [], "intake": {}}),
            "prose_rhythm_pass": _Out(None, raw="Revised draft body."),
        }
    )
    out = await handler.execute(_Step("v2"), ctx)
    assert out.json["ok"] is True
    assert out.json["quality_findings"] == [
        {"rule": "register", "detail": "clean", "penalty": 0}
    ]


@pytest.mark.asyncio
async def test_semantic_judge_all_omitted_when_ids_outside_eligible_set() -> None:
    async def caller(**kwargs: Any) -> str:
        if kwargs.get("which") == "pin":
            return json.dumps(_pin_payload("drafter_openai"))
        return json.dumps(_quality_payload("drafter_openai"))

    handler = semantic_judge.WritingSemanticJudgeHandler(caller=caller)
    published: list[Any] = []
    handler._publish_bus_event = lambda _ctx, event: published.append(event)  # type: ignore[method-assign]
    ctx = _Ctx(
        {
            "assemble": _Out({"pin_ledger": [], "intake": {}}),
            "drafter_anthropic": _Out(None, raw="Backed."),
        }
    )
    out = await handler.execute(_Step("candidates"), ctx)
    assert out.json["judge_unavailable"] is True
    assert out.json["reason"] == "all_omitted"
    assert len(published) == 1
    assert published[0].payload["reason"] == "all_omitted"


@pytest.mark.asyncio
async def test_semantic_judge_no_candidates_publishes_failed() -> None:
    handler = semantic_judge.WritingSemanticJudgeHandler()
    published: list[Any] = []
    handler._publish_bus_event = lambda _ctx, event: published.append(event)  # type: ignore[method-assign]
    ctx = _Ctx({"assemble": _Out({"pin_ledger": [], "intake": {}})})
    out = await handler.execute(_Step("candidates"), ctx)
    assert out.json["reason"] == "no_candidates"
    assert len(published) == 1
    assert published[0].signal == "writing.judge.failed"
    assert published[0].payload["reason"] == "no_candidates"


@pytest.mark.asyncio
async def test_semantic_judge_reviser_failed_publishes_failed() -> None:
    handler = semantic_judge.WritingSemanticJudgeHandler()
    published: list[Any] = []
    handler._publish_bus_event = lambda _ctx, event: published.append(event)  # type: ignore[method-assign]
    ctx = _Ctx(
        {
            "assemble": _Out({"pin_ledger": [], "intake": {}}),
            "prose_rhythm_pass": _Out(None, raw="", error="reviser_error"),
        }
    )
    out = await handler.execute(_Step("v2"), ctx)
    assert out.json["reason"] == "reviser_failed"
    assert published[0].signal == "writing.judge.failed"


@pytest.mark.asyncio
async def test_semantic_judge_empty_draft_publishes_failed() -> None:
    handler = semantic_judge.WritingSemanticJudgeHandler()
    published: list[Any] = []
    handler._publish_bus_event = lambda _ctx, event: published.append(event)  # type: ignore[method-assign]
    ctx = _Ctx(
        {
            "assemble": _Out({"pin_ledger": [], "intake": {}}),
            "prose_rhythm_pass": _Out(None, raw="   "),
        }
    )
    out = await handler.execute(_Step("v2"), ctx)
    assert out.json["reason"] == "empty_draft"
    assert published[0].signal == "writing.judge.failed"


@pytest.mark.asyncio
async def test_semantic_judge_schema_invalid_unavailable() -> None:
    async def caller(**kwargs: Any) -> str:
        if kwargs.get("which") == "pin":
            return json.dumps({"candidates": [{"candidate_id": "drafter_anthropic"}]})
        return json.dumps(_quality_payload("drafter_anthropic"))

    handler = semantic_judge.WritingSemanticJudgeHandler(caller=caller)
    published: list[Any] = []
    handler._publish_bus_event = lambda _ctx, event: published.append(event)  # type: ignore[method-assign]
    ctx = _Ctx(
        {
            "assemble": _Out({"pin_ledger": [], "intake": {}}),
            "drafter_anthropic": _Out(None, raw="Backed."),
        }
    )
    out = await handler.execute(_Step("candidates"), ctx)
    assert out.json["judge_unavailable"] is True
    assert len(published) == 1
    assert published[0].signal == "writing.judge.failed"


@pytest.mark.asyncio
async def test_semantic_judge_empty_verifier_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = semantic_judge.WritingSemanticJudgeHandler()
    published: list[Any] = []
    handler._publish_bus_event = lambda _ctx, event: published.append(event)  # type: ignore[method-assign]
    monkeypatch.setattr(
        semantic_judge,
        "load_writing_models",
        lambda: {"verifier": ""},
    )
    ctx = _Ctx(
        {
            "assemble": _Out({"pin_ledger": [], "intake": {}}),
            "drafter_anthropic": _Out(None, raw="Backed."),
        }
    )
    out = await handler.execute(_Step("candidates"), ctx)
    assert out.json["judge_unavailable"] is True
    assert out.json["reason"] == "verifier_model_missing"
    assert out.json["refused"] == "judge_unavailable"
    assert len(published) == 1
    assert published[0].signal == "writing.judge.failed"
    assert published[0].payload["reason"] == "verifier_model_missing"


@pytest.mark.asyncio
async def test_semantic_judge_transport_failure_publishes_failed() -> None:
    async def fake_call_model(*_args: Any, **_kwargs: Any) -> MagicMock:
        raise RuntimeError("transport_down")

    handler = semantic_judge.WritingSemanticJudgeHandler()
    handler._call_model = fake_call_model  # type: ignore[method-assign]
    published: list[Any] = []
    handler._publish_bus_event = lambda _ctx, event: published.append(event)  # type: ignore[method-assign]
    ctx = _Ctx(
        {
            "assemble": _Out({"pin_ledger": [], "intake": {}}),
            "drafter_anthropic": _Out(None, raw="Backed."),
        }
    )
    out = await handler.execute(_Step("candidates"), ctx)
    assert out.json["judge_unavailable"] is True
    assert published[0].signal == "writing.judge.failed"
    assert published[0].payload["reason"] == "RuntimeError"


def test_quality_judgment_rejects_out_of_range_penalty() -> None:
    judge_schema = importlib.import_module("writing_v1_handlers.judge_schema")
    err = judge_schema.quality_judgment_error(
        {"findings": [{"rule": "r", "detail": "d", "penalty": -1}]},
        multi_candidate=False,
    )
    assert err == "finding_penalty_range"


def test_judge_event_signals() -> None:
    completed = judge_events.WritingJudgeCompleted(
        invocation="candidates", model_id="qwen3-5-9b-q8-0-131072"
    )
    failed = judge_events.WritingJudgeFailed(invocation="v2", reason="transport")
    assert completed.signal == "writing.judge.completed"
    assert failed.signal == "writing.judge.failed"


@pytest.mark.asyncio
async def test_semantic_judge_pin_json_schema_candidates_invocation() -> None:
    pin_schemas: list[dict[str, Any]] = []

    async def fake_call_model(_model_id: str, *_args: Any, **kwargs: Any) -> MagicMock:
        schema = kwargs.get("json_schema")
        if len(pin_schemas) == 0:
            pin_schemas.append(schema)
        content = (
            json.dumps(_pin_payload("drafter_anthropic"))
            if len(pin_schemas) == 1
            else json.dumps(_quality_payload("drafter_anthropic"))
        )
        result = MagicMock()
        result.content = content
        return result

    handler = semantic_judge.WritingSemanticJudgeHandler()
    handler._call_model = fake_call_model  # type: ignore[method-assign]
    handler._publish_bus_event = lambda _ctx, _event: None  # type: ignore[method-assign]
    ctx = _Ctx(
        {
            "assemble": _Out({"pin_ledger": [], "intake": {}}),
            "drafter_anthropic": _Out(None, raw="Backed."),
        }
    )
    await handler.execute(_Step("candidates"), ctx)
    assert len(pin_schemas) == 1
    schema = pin_schemas[0]
    assert schema.get("required") == ["candidates"]
    props = schema.get("properties") or {}
    assert "candidates" in props
    assert "pins" not in props
    assert "clauses" not in props


@pytest.mark.asyncio
async def test_semantic_judge_pin_json_schema_v2_invocation() -> None:
    pin_schemas: list[dict[str, Any]] = []

    async def fake_call_model(_model_id: str, *_args: Any, **kwargs: Any) -> MagicMock:
        schema = kwargs.get("json_schema")
        if len(pin_schemas) == 0:
            pin_schemas.append(schema)
        content = (
            json.dumps(_v2_pin_payload())
            if len(pin_schemas) == 1
            else json.dumps(_v2_quality_payload())
        )
        result = MagicMock()
        result.content = content
        return result

    handler = semantic_judge.WritingSemanticJudgeHandler()
    handler._call_model = fake_call_model  # type: ignore[method-assign]
    handler._publish_bus_event = lambda _ctx, _event: None  # type: ignore[method-assign]
    ctx = _Ctx(
        {
            "assemble": _Out({"pin_ledger": [], "intake": {}}),
            "prose_rhythm_pass": _Out(None, raw="Revised draft body."),
        }
    )
    await handler.execute(_Step("v2"), ctx)
    assert len(pin_schemas) == 1
    schema = pin_schemas[0]
    assert set(schema.get("required") or []) == {"pins", "clauses"}
    props = schema.get("properties") or {}
    assert "pins" in props
    assert "clauses" in props
    assert "candidates" not in props


@pytest.mark.asyncio
async def test_semantic_judge_quality_json_schema_candidates_invocation() -> None:
    quality_schemas: list[dict[str, Any]] = []
    call_count = 0

    async def fake_call_model(_model_id: str, *_args: Any, **kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            quality_schemas.append(kwargs.get("json_schema"))
        content = (
            json.dumps(_pin_payload("drafter_anthropic"))
            if call_count == 1
            else json.dumps(_quality_payload("drafter_anthropic"))
        )
        result = MagicMock()
        result.content = content
        return result

    handler = semantic_judge.WritingSemanticJudgeHandler()
    handler._call_model = fake_call_model  # type: ignore[method-assign]
    handler._publish_bus_event = lambda _ctx, _event: None  # type: ignore[method-assign]
    ctx = _Ctx(
        {
            "assemble": _Out({"pin_ledger": [], "intake": {}}),
            "drafter_anthropic": _Out(None, raw="Backed."),
        }
    )
    await handler.execute(_Step("candidates"), ctx)
    assert len(quality_schemas) == 1
    schema = quality_schemas[0]
    assert schema.get("required") == ["findings"]
    findings = (schema.get("properties") or {}).get("findings") or {}
    item = findings.get("items") or {}
    assert set(item.get("required") or []) == {
        "candidate_id",
        "rule",
        "detail",
        "penalty",
    }
    cid = (item.get("properties") or {}).get("candidate_id") or {}
    assert cid.get("minLength") == 1


@pytest.mark.asyncio
async def test_semantic_judge_quality_json_schema_v2_invocation() -> None:
    quality_schemas: list[dict[str, Any]] = []
    call_count = 0

    async def fake_call_model(_model_id: str, *_args: Any, **kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            quality_schemas.append(kwargs.get("json_schema"))
        content = (
            json.dumps(_v2_pin_payload())
            if call_count == 1
            else json.dumps(_v2_quality_payload())
        )
        result = MagicMock()
        result.content = content
        return result

    handler = semantic_judge.WritingSemanticJudgeHandler()
    handler._call_model = fake_call_model  # type: ignore[method-assign]
    handler._publish_bus_event = lambda _ctx, _event: None  # type: ignore[method-assign]
    ctx = _Ctx(
        {
            "assemble": _Out({"pin_ledger": [], "intake": {}}),
            "prose_rhythm_pass": _Out(None, raw="Revised draft body."),
        }
    )
    await handler.execute(_Step("v2"), ctx)
    assert len(quality_schemas) == 1
    schema = quality_schemas[0]
    assert schema.get("required") == ["findings"]
    findings = (schema.get("properties") or {}).get("findings") or {}
    item = findings.get("items") or {}
    assert set(item.get("required") or []) == {"rule", "detail", "penalty"}
    assert "candidate_id" not in (item.get("properties") or {})


def test_semantic_judge_source_has_no_bare_qwen_alias() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "handlers" / "semantic_judge.py"
    ).read_text(encoding="utf-8")
    assert 'ModelId.parse("qwen3-5-9b")' not in source
    assert '"qwen3-5-9b"' not in source
    assert "load_writing_models" in source
