"""Executor-level OpenAI reviewer path (review_openai + review_openai_wait)."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from systems.pipeline.core.conditions import ConditionEvaluator
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

seat_dispatch = importlib.import_module("writing_v1_handlers.seat_dispatch")
seat_wait = importlib.import_module("writing_v1_handlers.seat_wait")
finalize = importlib.import_module("writing_v1_handlers.finalize")

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _clear_openai_posted() -> None:
    seat_dispatch._POSTED.clear()
    yield
    seat_dispatch._POSTED.clear()

_REVIEW_JSON = {
    "verdict": "ship",
    "findings": [],
    "clauses": [],
}


class _Out:
    def __init__(self, payload: dict) -> None:
        self.json = payload
        self.error = None
        self.model_id = None
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.model_call_count = 0


class _Ctx:
    def __init__(self, outputs: dict, options: dict | None = None) -> None:
        self.outputs = outputs
        self.options = options or {
            "dispatch_thread_id": "15790",
            "seat_timeout_s": 600,
        }
        self.execution_id = "run-openai-review"


class _Step:
    name = "review"


class _PostResp:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _GetResp:
    def __init__(self, data: dict[str, Any]) -> None:
        self.content = True
        self.status_code = 200
        self._data = data

    def json(self) -> dict[str, Any]:
        return self._data


class _StargateTransport:
    def __init__(
        self,
        *,
        admit_status: int = 202,
        admit_body: dict[str, Any] | None = None,
        poll_sequence: list[dict[str, Any]] | None = None,
    ) -> None:
        self.admit_status = admit_status
        self.admit_body = admit_body or {
            "execution_id": "exec-1",
            "status": "running",
        }
        self.poll_sequence = list(poll_sequence or [])
        self.posts: list[dict[str, Any]] = []
        self.gets: list[str] = []
        self._poll_index = 0

    async def post(self, path: str, json: dict[str, Any]) -> _PostResp:
        self.posts.append({"path": path, "json": json})
        model = str(json.get("model", ""))
        if model.startswith("anthropic/"):
            self.posts.append({"anthropic": True})
        if model.startswith("cdp/"):
            self.posts.append({"cdp": True})
        return _PostResp(self.admit_status, dict(self.admit_body))

    async def get(self, path: str, params: dict[str, Any] | None = None) -> _GetResp:
        self.gets.append(path)
        if self._poll_index < len(self.poll_sequence):
            data = self.poll_sequence[self._poll_index]
            self._poll_index += 1
        else:
            data = self.poll_sequence[-1] if self.poll_sequence else {"status": "running"}
        return _GetResp(data)


class _BusTransport:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    async def post(self, path: str, json: dict[str, Any], headers: dict | None = None) -> _PostResp:
        self.posts.append({"path": path, "json": json, "headers": headers or {}})
        return _PostResp(201, {})


class _Clock:
    def __init__(self, *, step: float = 5.0) -> None:
        self.t = 0.0
        self.step = step

    def __call__(self) -> float:
        return self.t

    async def sleep(self, _seconds: float) -> None:
        self.t += self.step


def _assemble_openai_writer(*, sensitivity: str = "sensitive") -> dict[str, Any]:
    return {
        "refused": None,
        "writer_seat": "cdp",
        "reviewer_seat": "openai",
        "writer_model": "anthropic/claude-sonnet-5-5",
        "reviewer_model": "openai/gpt-5.5",
        "documents_block": "",
        "brief_block": "",
        "pin_ledger": [],
        "dispatch_thread_id": "15790",
        "sensitivity": sensitivity,
    }


def _draft() -> dict[str, Any]:
    return {
        "draft": "Draft body.",
        "claims": [],
        "need": [],
        "dispositions": [],
    }


def _yaml_by_name() -> dict[str, Any]:
    raw = yaml.safe_load(
        (_V1 / "writer-specialist-v1.yaml").read_text(encoding="utf-8")
    )
    return {step.name: step for step in PipelineSpec(**raw).steps}


def _stub_cdp_review_steps(outputs: dict) -> None:
    outputs.setdefault("review_dispatch", _Out({}))
    outputs.setdefault("review_wait", _Out({}))


def _assert_openai_only_steps_run(by_name: dict, outputs: dict, options: dict) -> None:
    if "review_openai" not in by_name:
        pytest.skip("writer-specialist-v1 uses local fact_ceiling_audit; no review_openai step")
    _stub_cdp_review_steps(outputs)
    evaluator = ConditionEvaluator()
    if "review_dispatch" in by_name:
        assert (
            evaluator.evaluate(by_name["review_dispatch"].condition, outputs, options)
            is False
        )
    if "review_wait" in by_name:
        assert (
            evaluator.evaluate(by_name["review_wait"].condition, outputs, options)
            is False
        )
    if "review_local" in by_name:
        assert (
            evaluator.evaluate(by_name["review_local"].condition, outputs, options)
            is False
        )
    assert evaluator.evaluate(by_name["review_openai"].condition, outputs, options) is True
    if "review_openai_wait" in by_name:
        assert (
            evaluator.evaluate(by_name["review_openai_wait"].condition, outputs, options)
            is True
        )


async def _run_openai_review_path(
    transport: _StargateTransport,
    *,
    bus: Any | None = None,
    sidecar_writer: Any | None = None,
    clock: _Clock | None = None,
    sensitivity: str = "sensitive",
) -> tuple[dict, dict, dict]:
    by_name = _yaml_by_name()
    ctx = _Ctx(
        {
            "assemble": _Out(_assemble_openai_writer(sensitivity=sensitivity)),
            "draft": _Out(_draft()),
            "independence": _Out({"refused": None, "independence": "full"}),
        }
    )
    _assert_openai_only_steps_run(by_name, ctx.outputs, ctx.options)

    dispatch_handler = seat_dispatch.WritingReviewOpenaiHandler(
        client=transport,
        bus_client=bus,
        sidecar_writer=sidecar_writer,
    )
    openai_out = await dispatch_handler.execute(_Step(), ctx)
    ctx.outputs["review_openai"] = _Out(openai_out.json or {})

    clk = clock or _Clock()
    wait_handler = seat_wait.WritingReviewOpenaiWaitHandler(
        client=transport,
        bus_client=bus,
        now=clk,
        sleep=clk.sleep,
    )
    wait_out = await wait_handler.execute(_Step(), ctx)
    ctx.outputs["review_openai_wait"] = _Out(wait_out.json or {})

    selected = await seat_wait.WritingSeatSelectHandler().execute(_Step(), ctx)
    ctx.outputs["review"] = _Out(selected.json or {})
    ctx.outputs["provenance_check"] = _Out({"violations": [], "pass": True})
    fin = await finalize.WritingFinalizeHandler().execute(_Step(), ctx)
    return openai_out.json or {}, wait_out.json or {}, fin.json or {}


@pytest.mark.asyncio
async def test_openai_reviewer_success_202_admit_then_poll() -> None:
    transport = _StargateTransport(
        poll_sequence=[
            {"status": "running"},
            {"status": "completed", "result_text": json.dumps(_REVIEW_JSON)},
        ]
    )
    _admit, wait_json, fin = await _run_openai_review_path(transport)
    assert transport.posts[0]["path"] == "/api/v1/team/dispatch"
    assert len(transport.posts) == 1
    assert not any(p.get("anthropic") for p in transport.posts)
    assert not any(p.get("cdp") for p in transport.posts)
    assert wait_json["verdict"] == "ship"
    assert fin["review"]["status"] == "ok"
    assert len(transport.gets) >= 1


@pytest.mark.asyncio
async def test_openai_reviewer_quota_at_admit_held_bus_report() -> None:
    transport = _StargateTransport(
        admit_status=429,
        admit_body={"error_code": "insufficient_quota"},
    )
    bus = _BusTransport()
    ctx = _Ctx(
        {
            "assemble": _Out(_assemble_openai_writer()),
            "draft": _Out(_draft()),
        }
    )
    handler = seat_dispatch.WritingReviewOpenaiHandler(client=transport, bus_client=bus)
    openai_out = await handler.execute(_Step(), ctx)
    assert openai_out.json["status"] == "held_reviewer_unfunded"
    assert len(transport.posts) == 1
    assert bus.posts and bus.posts[0]["path"] == "/turns"


def _federated_quota_detail() -> dict[str, Any]:
    from universal_protocol import error_envelope

    return error_envelope(
        code="upstream_rate_limited",
        message="Remote provider rejected request: 429 (gateway_id=openai-test)",
        source="master",
        retryable=False,
        data={
            "status_code": 429,
            "gateway_id": "openai-test",
            "upstream_error": {
                "error": {
                    "type": "insufficient_quota",
                    "code": "insufficient_quota",
                }
            },
        },
    )


def _failed_execution_monitor_payload() -> dict[str, Any]:
    from systems.pipeline.core.dag import PipelineExecutionError
    from systems.pipeline.core.execution.async_tracker import PipelineExecutionTracker
    from systems.pipeline.core.execution.proxy_client import ProxyClientError
    from systems.pipeline.core.executor import _normalize_pipeline_exception
    from systems.proxy.routers.api.execution_monitor import tracker_monitor_payload

    inner = ProxyClientError(
        "Stargate returned 429: Remote provider rejected request",
        status_code=429,
        detail=_federated_quota_detail(),
    )
    try:
        raise PipelineExecutionError("Step 'review-openai' failed: quota") from inner
    except PipelineExecutionError as wrapped:
        exc = wrapped
    code, message, data = _normalize_pipeline_exception(exc)
    tracker = PipelineExecutionTracker()
    tracker.register_execution(
        execution_id="exec-quota",
        pipeline="writer-specialist-v1",
        started_at="2026-01-01T00:00:00Z",
    )
    tracker.fail_execution("exec-quota", code=code, message=message, data=data)
    record = tracker.get("exec-quota")
    assert record is not None
    return tracker_monitor_payload(record)


def _native_provider_quota_monitor_payload(*, quota_code: str = "insufficient_quota") -> dict[str, Any]:
    """Frontier native loop: RuntimeError provider-native 429 with JSON body, data=null."""
    import httpx
    from systems.pipeline.core.dag import PipelineExecutionError
    from systems.pipeline.core.execution.async_tracker import PipelineExecutionTracker
    from systems.pipeline.core.executor import _normalize_pipeline_exception
    from systems.proxy.routers.api.execution_monitor import tracker_monitor_payload

    body = json.dumps(
        {"error": {"type": quota_code, "code": quota_code, "message": "quota"}}
    )
    native = RuntimeError(f"provider-native 429: {body}")
    try:
        req = httpx.Request("POST", "https://example.test/v1/chat/completions")
        resp = httpx.Response(429, request=req, text=body)
        raise httpx.HTTPStatusError("429", request=req, response=resp) from native
    except httpx.HTTPStatusError as http_exc:
        try:
            raise RuntimeError(f"provider-native 429: {body}") from http_exc
        except RuntimeError as provider_exc:
            try:
                raise PipelineExecutionError(
                    f"Step 'review-openai' failed: {provider_exc}"
                ) from provider_exc
            except PipelineExecutionError as wrapped:
                exc = wrapped
    code, message, data = _normalize_pipeline_exception(exc)
    assert data is None
    tracker = PipelineExecutionTracker()
    tracker.register_execution(
        execution_id="exec-native-quota",
        pipeline="writer-specialist-v1",
        started_at="2026-01-01T00:00:00Z",
    )
    tracker.fail_execution("exec-native-quota", code=code, message=message, data=data)
    record = tracker.get("exec-native-quota")
    assert record is not None
    return tracker_monitor_payload(record)


@pytest.mark.asyncio
async def test_openai_reviewer_native_runtime_quota_held_with_reason() -> None:
    failed_payload = _native_provider_quota_monitor_payload()
    transport = _StargateTransport(
        admit_status=202,
        admit_body={"execution_id": "exec-native-quota", "status": "running"},
        poll_sequence=[failed_payload],
    )
    bus = _BusTransport()
    _admit, wait_json, fin = await _run_openai_review_path(transport, bus=bus)
    assert wait_json["status"] == "held_reviewer_unfunded"
    assert wait_json["reason"] == "insufficient_quota"
    assert len(bus.posts) == 1
    assert bus.posts[0]["path"] == "/turns"
    assert "insufficient_quota" in str(bus.posts[0]["json"].get("body", ""))
    assert fin["review"]["status"] == "held_reviewer_unfunded"


@pytest.mark.asyncio
async def test_openai_reviewer_native_rate_limit_failed_zero_bus_posts() -> None:
    failed_payload = _native_provider_quota_monitor_payload(
        quota_code="rate_limit_exceeded"
    )
    transport = _StargateTransport(
        admit_status=202,
        admit_body={"execution_id": "exec-native-rate", "status": "running"},
        poll_sequence=[failed_payload],
    )
    bus = _BusTransport()
    _admit, wait_json, fin = await _run_openai_review_path(transport, bus=bus)
    assert wait_json.get("status") != "held_reviewer_unfunded"
    assert wait_json["refused"] == "review_transport_failed"
    assert not bus.posts
    assert fin["review"]["status"] == "failed"


@pytest.mark.asyncio
async def test_openai_reviewer_quota_production_execution_record_held() -> None:
    failed_payload = _failed_execution_monitor_payload()
    transport = _StargateTransport(
        admit_status=202,
        admit_body={"execution_id": "exec-quota", "status": "running"},
        poll_sequence=[failed_payload],
    )
    bus = _BusTransport()
    _admit, wait_json, fin = await _run_openai_review_path(transport, bus=bus)
    assert wait_json["status"] == "held_reviewer_unfunded"
    assert wait_json.get("reason") == "insufficient_quota"
    assert len(bus.posts) == 1
    assert bus.posts[0]["path"] == "/turns"
    assert fin["review"]["status"] == "held_reviewer_unfunded"
    assert fin["ship_gate"]["pass"] is False


@pytest.mark.asyncio
async def test_openai_reviewer_rate_limit_failed_not_held() -> None:
    from universal_protocol import error_envelope

    detail = error_envelope(
        code="upstream_rate_limited",
        message="Remote provider rejected request: 429 (gateway_id=openai-test)",
        source="master",
        retryable=True,
        data={
            "status_code": 429,
            "gateway_id": "openai-test",
            "upstream_error": {"error": {"type": "rate_limit", "code": "rate_limit_exceeded"}},
        },
    )
    from systems.pipeline.core.dag import PipelineExecutionError
    from systems.pipeline.core.execution.async_tracker import PipelineExecutionTracker
    from systems.pipeline.core.execution.proxy_client import ProxyClientError
    from systems.pipeline.core.executor import _normalize_pipeline_exception
    from systems.proxy.routers.api.execution_monitor import tracker_monitor_payload

    inner = ProxyClientError("Stargate returned 429", status_code=429, detail=detail)
    try:
        raise PipelineExecutionError("Step 'review-openai' failed") from inner
    except PipelineExecutionError as wrapped:
        exc = wrapped
    code, message, data = _normalize_pipeline_exception(exc)
    tracker = PipelineExecutionTracker()
    tracker.register_execution(
        execution_id="exec-rate",
        pipeline="writer-specialist-v1",
        started_at="2026-01-01T00:00:00Z",
    )
    tracker.fail_execution("exec-rate", code=code, message=message, data=data)
    monitor = tracker_monitor_payload(tracker.get("exec-rate"))
    transport = _StargateTransport(
        admit_status=202,
        admit_body={"execution_id": "exec-rate", "status": "running"},
        poll_sequence=[monitor],
    )
    _admit, wait_json, fin = await _run_openai_review_path(transport)
    assert wait_json.get("status") != "held_reviewer_unfunded"
    assert wait_json["refused"] == "review_transport_failed"
    assert fin["review"]["status"] == "failed"


@pytest.mark.asyncio
async def test_openai_reviewer_bus_report_without_injected_client() -> None:
    transport = _StargateTransport(
        admit_status=429,
        admit_body={"error_code": "insufficient_quota"},
    )
    bus = _BusTransport()

    class _ClientCtx:
        def __init__(self, inner: _BusTransport) -> None:
            self._inner = inner

        async def __aenter__(self) -> _BusTransport:
            return self._inner

        async def __aexit__(self, *_a: object) -> None:
            return None

    with patch(
        "transport_utils.make_async_client",
        return_value=_ClientCtx(bus),
    ):
        handler = seat_dispatch.WritingReviewOpenaiHandler(client=transport)
        ctx = _Ctx(
            {
                "assemble": _Out(_assemble_openai_writer()),
                "draft": _Out(_draft()),
            }
        )
        out = await handler.execute(_Step(), ctx)
    assert out.json["status"] == "held_reviewer_unfunded"
    assert len(bus.posts) == 1
    assert bus.posts[0]["path"] == "/turns"


@pytest.mark.asyncio
async def test_openai_reviewer_timeout_failed_not_held() -> None:
    clock = _Clock(step=400.0)
    transport = _StargateTransport(poll_sequence=[{"status": "running"}] * 20)
    ctx_options = {"dispatch_thread_id": "15790", "seat_timeout_s": 30}
    ctx = _Ctx(
        {
            "assemble": _Out(_assemble_openai_writer()),
            "draft": _Out(_draft()),
            "independence": _Out({"refused": None, "independence": "full"}),
        },
        options=ctx_options,
    )
    dispatch_handler = seat_dispatch.WritingReviewOpenaiHandler(client=transport)
    openai_out = await dispatch_handler.execute(_Step(), ctx)
    ctx.outputs["review_openai"] = _Out(openai_out.json or {})
    wait_handler = seat_wait.WritingReviewOpenaiWaitHandler(
        client=transport,
        now=clock,
        sleep=clock.sleep,
    )
    wait_out = await wait_handler.execute(_Step(), ctx)
    assert wait_out.json["error"] == "timeout"
    assert wait_out.json.get("status") != "held_reviewer_unfunded"


@pytest.mark.asyncio
async def test_openai_reviewer_sensitive_brief_uses_sidecar_pointer() -> None:
    transport = _StargateTransport(
        poll_sequence=[
            {"status": "completed", "result_text": json.dumps(_REVIEW_JSON)},
        ]
    )
    sidecars: list[tuple[str, str]] = []

    def _writer(prompt: str, key: str) -> str:
        sidecars.append((prompt, key))
        return "cortex://notes/system/threads/writer-prompts/deadbeef.txt"

    await _run_openai_review_path(
        transport,
        sidecar_writer=_writer,
        sensitivity="sensitive",
    )
    body = transport.posts[0]["json"]
    assert "prompt" not in body
    assert body.get("sidecar_ref", "").startswith("cortex://")
    assert body.get("bus_body") == "pointer_only"
    assert sidecars


@pytest.mark.asyncio
async def test_openai_reviewer_duplicate_dispatch_suppressed() -> None:
    transport = _StargateTransport(
        poll_sequence=[{"status": "completed", "result_text": json.dumps(_REVIEW_JSON)}],
    )
    ctx = _Ctx(
        {
            "assemble": _Out(_assemble_openai_writer()),
            "draft": _Out(_draft()),
        }
    )
    handler = seat_dispatch.WritingReviewOpenaiHandler(client=transport)
    first = await handler.execute(_Step(), ctx)
    assert first.json.get("duplicate_suppressed") is not True
    second = await handler.execute(_Step(), ctx)
    assert second.json.get("duplicate_suppressed") is True
    assert len(transport.posts) == 1


def test_resolve_openai_reviewer_refuses_uncarded_model() -> None:
    assemble = importlib.import_module("writing_v1_handlers.assemble")
    with pytest.raises(ValueError, match="not a carded"):
        assemble.resolve_openai_reviewer_model({"openai": "openai/gpt-4o-mini"})


@pytest.mark.asyncio
async def test_openai_reviewer_other_execution_error_not_held() -> None:
    transport = _StargateTransport(
        poll_sequence=[
            {"status": "failed", "error": {"code": "pipeline_execution_cancelled"}},
        ]
    )
    _admit, wait_json, fin = await _run_openai_review_path(transport)
    assert wait_json.get("status") != "held_reviewer_unfunded"
    assert wait_json["refused"] == "review_transport_failed"
    assert fin["review"]["status"] == "failed"
