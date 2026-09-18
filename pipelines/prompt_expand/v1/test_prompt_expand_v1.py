"""Offline unit tests for prompt-expand v1 handlers and tables."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from systems.pipeline.core.events.prompt_expand import (
    ExpandAdmitted,
    ExpandAuthorCompleted,
    ExpandCompleted,
    ExpandRetrieveCompleted,
)
from universal_event_bus.events.validation import validate_event_signal

_HANDLERS_PATH = Path(__file__).resolve().parent / "handlers" / "profile_lookup.py"
_spec = importlib.util.spec_from_file_location(
    "prompt_expand_profile_lookup", _HANDLERS_PATH
)
assert _spec and _spec.loader
_handlers = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _handlers
_spec.loader.exec_module(_handlers)

PromptExpandClassifyRetrieveHandler = _handlers.PromptExpandClassifyRetrieveHandler
PromptExpandFormatOutputHandler = _handlers.PromptExpandFormatOutputHandler
PromptExpandProfileLookupHandler = _handlers.PromptExpandProfileLookupHandler
PromptExpandRetrieveHandler = _handlers.PromptExpandRetrieveHandler
PromptExpandValidateOptionsHandler = _handlers.PromptExpandValidateOptionsHandler
_EMPTY_RETRIEVAL_SENTINEL = _handlers._EMPTY_RETRIEVAL_SENTINEL
_lookup_profile = _handlers._lookup_profile

pytestmark = pytest.mark.offline

_TABLES_PATH = Path(__file__).resolve().parent / "profile_tables.yaml"


class _Step:
    id = "test_step"
    handler_timeout_seconds = 90
    timeout_seconds = 300

    def get_domain_field(self, key: str, default=None):
        return default


class _Out:
    def __init__(self, raw: str = "", json: dict | None = None):
        self.raw = raw
        self.json = json or {}


class _Ctx:
    execution_id = "exec-test"
    source_text = "Expand this TASK"
    dispatch_thread_id = None
    _registry = None

    def __init__(self, options: dict | None = None, outputs: dict | None = None):
        self.options = options or {}
        self._outputs = outputs or {}

    @property
    def pipeline(self):
        class _P:
            id = "prompt-expand"
            domain = "prompt_expand"
            source_search_path = str(Path(__file__).resolve().parent)

        return _P()

    def get_output(self, name: str):
        return self._outputs.get(name)


def _base_options(**overrides):
    base = {
        "contract": "implement",
        "stage": "g5",
        "executor_tier": "frontier",
        "target": "cursor",
        "delivery": "prompt",
        "rag_fail": "abort",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_validate_rejects_missing_target() -> None:
    handler = PromptExpandValidateOptionsHandler()
    out = await handler.execute(_Step(), _Ctx(_base_options(target=None)))
    assert out.json["error"]["code"] == "expand.target_required"


@pytest.mark.asyncio
async def test_validate_rejects_grok_bot() -> None:
    handler = PromptExpandValidateOptionsHandler()
    out = await handler.execute(_Step(), _Ctx(_base_options(target="grok-bot")))
    assert out.json["error"]["code"] == "expand.target_unsupported"


@pytest.mark.asyncio
async def test_profile_lookup_implement_g5_elicitation_false() -> None:
    row = _lookup_profile("implement", "g5", "frontier")
    assert row is not None
    assert row["elicitation"] is False


@pytest.mark.asyncio
async def test_profile_lookup_miss_typed_reject(monkeypatch) -> None:
    monkeypatch.setattr(_handlers, "_tables_cache", {"profiles": [], "targets": []})
    handler = PromptExpandProfileLookupHandler()
    ctx = _Ctx(_base_options(contract="review", stage="g7", executor_tier="small"))
    out = await handler.execute(_Step(), ctx)
    assert out.json["error"]["code"] == "expand.profile_miss"


@pytest.mark.asyncio
async def test_profile_target_static_true() -> None:
    handler = PromptExpandProfileLookupHandler()
    out = await handler.execute(_Step(), _Ctx(_base_options(target="cdp")))
    assert out.json["target_static"] is True


@pytest.mark.asyncio
async def test_classify_stamp_empty_proceeds_priors_only() -> None:
    handler = PromptExpandClassifyRetrieveHandler()
    ctx = _Ctx(
        _base_options(rag_fail="stamp"),
        {
            "retrieve_context": _Out(
                raw=f"{_EMPTY_RETRIEVAL_SENTINEL}. The answer is generated from model knowledge only.",
                json={"attempts": 1, "timed_out": False, "upstream_error": False},
            ),
            "resolve_profile": _Out(json={"retrieve_scopes": ["llm_prompting"]}),
        },
    )
    out = await handler.execute(_Step(), ctx)
    assert out.json["provenance_mode"] == "PRIORS-ONLY"
    assert out.json["proceed"] is True


@pytest.mark.asyncio
async def test_classify_abort_empty_stops() -> None:
    handler = PromptExpandClassifyRetrieveHandler()
    ctx = _Ctx(
        _base_options(rag_fail="abort"),
        {
            "retrieve_context": _Out(
                raw=_EMPTY_RETRIEVAL_SENTINEL,
                json={"attempts": 1, "timed_out": False, "upstream_error": False},
            ),
            "resolve_profile": _Out(json={"retrieve_scopes": ["llm_prompting"]}),
        },
    )
    out = await handler.execute(_Step(), ctx)
    assert out.json["proceed"] is False
    assert out.error


@pytest.mark.asyncio
async def test_classify_timeout_degraded_author_proceeds() -> None:
    handler = PromptExpandClassifyRetrieveHandler()
    ctx = _Ctx(
        _base_options(rag_fail="abort"),
        {
            "retrieve_context": _Out(
                raw="",
                json={"timed_out": True, "attempts": 3, "upstream_error": False},
            ),
            "resolve_profile": _Out(json={"retrieve_scopes": ["llm_prompting"]}),
        },
    )
    out = await handler.execute(_Step(), ctx)
    assert out.json["rag_status"] == "deadline"
    assert out.json["provenance_mode"] == "DEGRADED"
    assert out.json["proceed"] is True
    assert out.json["attempts"] == 3


@pytest.mark.asyncio
async def test_format_dispatch_json_envelope_no_bus_fields() -> None:
    handler = PromptExpandFormatOutputHandler()
    ctx = _Ctx(
        _base_options(delivery="dispatch"),
        {
            "classify_retrieve": _Out(json={"rag_status": "ok", "provenance_mode": "NORMAL", "proceed": True, "attempts": 1}),
            "resolve_profile": _Out(
                json={
                    "contract": "implement",
                    "stage": "g5",
                    "executor_tier": "frontier",
                    "retrieve_scopes": ["llm_prompting"],
                    "target_static": True,
                    "allowed_doors": ["cortex", "team_dispatch"],
                    "elicitation": False,
                }
            ),
            "select_author": _Out(raw="Expanded TASK prime"),
        },
    )
    out = await handler.execute(_Step(), ctx)
    assert "fire_plan" in out.json
    assert out.json["fire_plan"]["delivery"] == "dispatch"
    assert "team_dispatch" not in json.dumps(out.json.get("fire_plan", {}))


@pytest.mark.asyncio
async def test_format_cdp_rejects_code_extra_in_rendered_output() -> None:
    handler = PromptExpandFormatOutputHandler()
    ctx = _Ctx(
        _base_options(target="cdp"),
        {
            "classify_retrieve": _Out(
                json={
                    "rag_status": "ok",
                    "provenance_mode": "NORMAL",
                    "proceed": True,
                    "attempts": 1,
                }
            ),
            "resolve_profile": _Out(
                json={
                    "contract": "implement",
                    "stage": "g5",
                    "executor_tier": "frontier",
                    "retrieve_scopes": ["llm_prompting"],
                    "target_static": True,
                    "allowed_doors": ["cortex", "fs"],
                    "elicitation": False,
                }
            ),
            "select_author": _Out(raw="Fire via team_dispatch on the bus"),
        },
    )
    out = await handler.execute(_Step(), ctx)
    assert out.json["error"]["code"] == "expand.cdp_code_extra_doors"
    assert out.error


@pytest.mark.asyncio
async def test_format_cdp_passes_clean_rendered_output() -> None:
    handler = PromptExpandFormatOutputHandler()
    ctx = _Ctx(
        _base_options(target="cdp"),
        {
            "classify_retrieve": _Out(
                json={
                    "rag_status": "ok",
                    "provenance_mode": "NORMAL",
                    "proceed": True,
                    "attempts": 1,
                }
            ),
            "resolve_profile": _Out(
                json={
                    "contract": "implement",
                    "stage": "g5",
                    "executor_tier": "frontier",
                    "retrieve_scopes": ["llm_prompting"],
                    "target_static": True,
                    "allowed_doors": ["cortex", "fs", "cdp_ask"],
                    "elicitation": False,
                }
            ),
            "select_author": _Out(raw="Use cortex and cdp_ask only"),
        },
    )
    out = await handler.execute(_Step(), ctx)
    assert out.error is None
    assert "cdp_ask" in out.raw


@pytest.mark.asyncio
async def test_format_cursor_allows_team_dispatch_in_rendered_output() -> None:
    handler = PromptExpandFormatOutputHandler()
    ctx = _Ctx(
        _base_options(target="cursor"),
        {
            "classify_retrieve": _Out(
                json={
                    "rag_status": "ok",
                    "provenance_mode": "NORMAL",
                    "proceed": True,
                    "attempts": 1,
                }
            ),
            "resolve_profile": _Out(
                json={
                    "contract": "implement",
                    "stage": "g5",
                    "executor_tier": "frontier",
                    "retrieve_scopes": ["llm_prompting"],
                    "target_static": True,
                    "allowed_doors": ["cortex", "team_dispatch"],
                    "elicitation": False,
                }
            ),
            "select_author": _Out(raw="Chain team_dispatch for the code lane"),
        },
    )
    out = await handler.execute(_Step(), ctx)
    assert out.error is None
    assert "team_dispatch" in out.raw


def test_profile_tables_no_top_level_version_key() -> None:
    raw = yaml.safe_load(_TABLES_PATH.read_text(encoding="utf-8"))
    assert "version" not in raw
    assert "schema_version" not in raw
    assert raw.get("table_version") == 1


def test_expand_events_validate_signal_pattern() -> None:
    validate_event_signal(
        ExpandAdmitted(
            pipeline_id="prompt-expand",
            execution_id="e1",
            contract="implement",
            stage="g5",
            executor_tier="frontier",
            target="cursor",
            delivery="prompt",
        ).signal
    )
    validate_event_signal(
        ExpandRetrieveCompleted(
            pipeline_id="prompt-expand",
            execution_id="e1",
            step_name="classify_retrieve",
            rag_status="ok",
            attempts=1,
            retrieve_scopes=["llm_prompting"],
        ).signal
    )
    validate_event_signal(
        ExpandAuthorCompleted(
            pipeline_id="prompt-expand",
            execution_id="e1",
            step_name="author_cursor",
            target="cursor",
        ).signal
    )
    validate_event_signal(
        ExpandCompleted(
            pipeline_id="prompt-expand",
            execution_id="e1",
            delivery="prompt",
            target="cursor",
            rag_status="ok",
            provenance_mode="NORMAL",
        ).signal
    )


def test_handlers_no_in_dag_bus_or_dispatch_calls() -> None:
    source = Path(__file__).resolve().parent / "handlers" / "profile_lookup.py"
    text = source.read_text(encoding="utf-8")
    assert "agent_bus" not in text
    assert "CallDynamicTool" not in text
    assert "cortex_dispatch" not in text


@pytest.mark.asyncio
async def test_retrieve_merged_options_omit_target(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_pipeline_call(step, _ctx):
        captured["pipeline_options"] = step.get_domain_field("pipeline_options", {})
        out = _Out(raw="ctx chunk", json={"retrieval": {}})
        out.latency_ms = 1.0
        return out

    monkeypatch.setattr(_handlers, "_PIPELINE_CALL_HANDLER", _handlers.PipelineCallHandler())
    monkeypatch.setattr(_handlers._PIPELINE_CALL_HANDLER, "execute", _fake_pipeline_call)

    handler = PromptExpandRetrieveHandler()
    ctx = _Ctx(
        _base_options(target="cdp"),
        {
            "resolve_profile": _Out(
                json={"retrieve_scopes": ["llm_prompting", "prompt_injection"]}
            )
        },
    )
    step = _Step()
    step.get_domain_field = lambda key, default=None: (  # type: ignore[method-assign]
        "expand" if key == "consumer_model_ref" else default
    )
    out = await handler.execute(step, ctx)
    merged = out.json["merged_options"]
    assert "target" not in merged
    assert merged["scope"] == ["llm_prompting", "prompt_injection"]
    assert captured["pipeline_options"]["scope"] == merged["scope"]
    assert out.json["attempts"] == 1


@pytest.mark.asyncio
async def test_retrieve_timeout_three_attempts_then_degraded(monkeypatch) -> None:
    import httpx

    calls = {"n": 0}

    async def _always_timeout(_step, _ctx):
        calls["n"] += 1
        raise httpx.ReadTimeout("rag-context slow")

    monkeypatch.setattr(_handlers, "_PIPELINE_CALL_HANDLER", _handlers.PipelineCallHandler())
    monkeypatch.setattr(_handlers._PIPELINE_CALL_HANDLER, "execute", _always_timeout)
    monkeypatch.setattr(_handlers, "_RETRIEVE_BACKOFF_SECONDS", 0.0)

    handler = PromptExpandRetrieveHandler()
    ctx = _Ctx(
        _base_options(),
        {"resolve_profile": _Out(json={"retrieve_scopes": ["llm_prompting"]})},
    )
    out = await handler.execute(_Step(), ctx)
    assert calls["n"] == 3
    assert out.json["attempts"] == 3
    assert out.json["timed_out"] is True
    assert out.raw == ""

    classify = PromptExpandClassifyRetrieveHandler()
    ctx._outputs["retrieve_context"] = _Out(raw=out.raw, json=out.json)
    classified = await classify.execute(_Step(), ctx)
    assert classified.json["rag_status"] == "deadline"
    assert classified.json["provenance_mode"] == "DEGRADED"
    assert classified.json["proceed"] is True
    assert classified.json["attempts"] == 3
