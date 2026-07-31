from __future__ import annotations

from typing import Never, cast, override

import pytest
from fastapi import Request

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import PipelineContext
from systems.pipeline.core.schemas import PipelineOptions, PipelineSpec, StepConfig


class _TestHandler(BaseHandler):
    step_type: str = "_test"

    @override
    async def execute(self, step: object, context: object) -> Never:  # pragma: no cover
        raise NotImplementedError()


from systems.pipeline.schemas import ModelRef


class _RegistryNoAliasKeys:
    """Registry stub where get_model_config always raises — only is_pipeline"
        "succeeds."""

    _pipeline_ids: set[str]

    def __init__(self, *, pipeline_ids: set[str]) -> None:
        self._pipeline_ids = pipeline_ids

    def get_model_config(
        self, model_ref: str, *, domain: str, search_path: str
    ) -> Never:
        _ = (model_ref, domain, search_path)
        raise KeyError("not an alias key")

    def is_pipeline(self, model_id: str) -> bool:
        return model_id in self._pipeline_ids


class _RegistryWithAlias:
    """Registry stub where get_model_config resolves one alias; is_pipeline is always"
        "False."""

    def __init__(self, *, alias: str, resolved: str) -> None:
        self._alias = alias
        self._resolved = resolved

    def get_model_config(
        self, model_ref: str, *, domain: str, search_path: str
    ) -> ModelRef:
        _ = (domain, search_path)
        if model_ref == self._alias:
            return ModelRef(model=self._resolved)
        raise KeyError(f"unknown ref: {model_ref}")

    def is_pipeline(self, model_id: str) -> bool:  # noqa: ARG002
        return False


def _make_request() -> Request:
    return Request(
        scope={
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 9999),
        }
    )


def _make_context(registry: object) -> PipelineContext:
    pipeline = PipelineSpec(
        id="p",
        version="1",
        type="answer_v1",
        steps=[],
        output="out",
        options=PipelineOptions(skip_token_counting=False),
        source_search_path="pipelines",
    )
    ctx = PipelineContext(
        pipeline=pipeline,
        source_text="",
        http_request=_make_request(),
        execution_id="exec-1",
        map_iteration_request_id=None,
    )
    ctx._registry = registry
    return ctx


def test_resolve_model_alias_passes_through_pipeline_ids() -> None:
    """Pipeline IDs not in the alias table are returned as-is (pipeline-as-service"
        "path)."""
    handler = _TestHandler()
    context = _make_context(_RegistryNoAliasKeys(pipeline_ids={"rag-context"}))
    assert handler._resolve_model_alias("rag-context", context) == "rag-context"


def test_resolve_model_alias_prefers_registry_over_pipeline_id() -> None:
    """When an ID exists as an alias key, registry resolution wins over is_pipeline.

    The is_pipeline fallback is only reached on KeyError; if get_model_config
    succeeds the alias-resolved model ID is returned without consulting is_pipeline.
    """
    handler = _TestHandler()
    # "rag-context" is a valid alias that resolves to a full inference model ID.
    # is_pipeline is never consulted because get_model_config succeeds first.
    context = _make_context(
        _RegistryWithAlias(alias="rag-context", resolved="phi-4-q4-k-m-16384")
    )
    assert handler._resolve_model_alias("rag-context", context) == "phi-4-q4-k-m-16384"


class _NoResolveHandler(_TestHandler):
    @override
    def _resolve_model_alias(self, model_id: str, context: object) -> str:
        raise AssertionError("_resolve_model_alias should not be called")


class _DummyProxyClient:
    async def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        execution_id: str,
        step_id: str,
        skip_token_counting: bool,
        timeout: float | None,
        map_iteration_request_id: str | None,
        **params: object,
    ) -> tuple[dict[str, object], str | None, str | None]:
        _ = (
            model,
            messages,
            execution_id,
            step_id,
            skip_token_counting,
            timeout,
            map_iteration_request_id,
            params,
        )
        response: dict[str, object] = {
            "choices": cast(
                object,
                [
                    {
                        "message": {"content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            ),
            "usage": cast(object, {"prompt_tokens": 1, "completion_tokens": 1}),
        }
        return (response, None, None)


@pytest.mark.asyncio
async def test_call_model_can_skip_alias_resolution_when_pre_resolved() -> None:
    handler = _NoResolveHandler()
    context = _make_context(object())
    context.proxy_client = _DummyProxyClient()

    # Avoid pydantic constructor signature issues around the reserved "from" field.
    step = StepConfig.model_validate(
        {
            "id": "get_context",
            "type": "generate",
            "handler_timeout_seconds": None,
            "timeout_seconds": None,
            "skip_token_counting": None,
            "from": None,
            "depends_on": [],
        }
    )

    result = await handler._call_model(
        "rag-context",
        "hi",
        step,
        context,
        system_prompt=None,
        model_id_is_resolved=True,
    )

    assert result.content == "ok"
    assert result.request_body["model"] == "rag-context"
