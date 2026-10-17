"""Unit matrix for explicit anthropic team_dispatch override admission."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from agent_seat import AgentMeta, HydrationBundle

from .admission import FrontierEndpointError
from .anthropic_override_gate import (
    REJECT_CODE,
    credit_gate_healthy,
    enforce_anthropic_override,
    evaluate_anthropic_override,
    reset_configured_cloud_proxy_url_cache,
    reset_credit_health_cache,
    set_anthropic_credit_health_probe,
)
from .service import FrontierGenerateRequest, build_dispatch_body

_OPUS = "anthropic/claude-opus-5"
_OPUS55 = "anthropic/claude-opus-5-5"
_OPENROUTER_SONNET = "openrouter/anthropic/claude-sonnet-5-5"
_SONNET55 = "anthropic/claude-sonnet-5-5"
_HAIKU55 = "anthropic/claude-haiku-5-5"
_FABLE = "anthropic/claude-fable-5-1"
_DISPATCH_THREAD = "test-dispatch-thread"
_STANDARD_MODELS = frozenset({_SONNET55, _HAIKU55})


@pytest.fixture(autouse=True)
def _reset_credit_health_probe() -> None:
    set_anthropic_credit_health_probe(None)
    reset_credit_health_cache()
    reset_configured_cloud_proxy_url_cache()
    yield
    set_anthropic_credit_health_probe(None)
    reset_credit_health_cache()
    reset_configured_cloud_proxy_url_cache()


def test_reviewer_opus_without_attestation_rejects() -> None:
    verdict = evaluate_anthropic_override(
        model=_OPUS,
        profile_provider="openai",
        profile_allowed_models=("openai/gpt-5.6-terra",),
    )
    assert not verdict.admitted
    assert verdict.code == REJECT_CODE


def test_reviewer_opus_with_cost_intent_admits() -> None:
    verdict = evaluate_anthropic_override(
        model=_OPUS,
        profile_provider="openai",
        profile_allowed_models=(),
        cost_intent="deliberate_high_cost",
        cost_intent_reason="operator authorized cross-family review",
    )
    assert verdict.admitted


def test_spawn_review_provenance_admits_without_cost_intent() -> None:
    verdict = evaluate_anthropic_override(
        model=_OPUS,
        profile_provider="openai",
        profile_allowed_models=(),
        spawn_review_provenance="generate_review_child",
    )
    assert verdict.admitted


def test_in_family_anthropic_profile_admits() -> None:
    verdict = evaluate_anthropic_override(
        model=_OPUS,
        profile_provider="anthropic",
        profile_allowed_models=(
            "anthropic/claude-opus-5",
            "anthropic/claude-opus-4-8",
            "anthropic/claude-sonnet-4-6",
        ),
    )
    assert verdict.admitted


def test_openai_explicit_model_noops() -> None:
    verdict = evaluate_anthropic_override(
        model="openai/gpt-5.6-terra",
        profile_provider="openai",
        profile_allowed_models=(),
    )
    assert verdict.admitted


def test_cursor_explicit_model_noops() -> None:
    verdict = evaluate_anthropic_override(
        model="cursor/claude-sonnet-5",
        profile_provider="cursor",
        profile_allowed_models=(),
    )
    assert verdict.admitted


def test_role_allowlist_alone_does_not_authorize_cross_family() -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        enforce_anthropic_override(
            request_id="req-cross",
            model=_OPUS,
            profile_provider="openai",
            profile_allowed_models=("openai/gpt-5.6-terra",),
        )
    err = exc_info.value
    assert err.code == REJECT_CODE
    assert err.field == "model"
    assert err.status_code == 422


@pytest.mark.parametrize("model", [_SONNET55, _HAIKU55])
def test_standard_model_admitted_when_credit_gate_healthy(model: str) -> None:
    verdict = evaluate_anthropic_override(
        model=model,
        profile_provider="openai",
        profile_allowed_models=(),
        credit_healthy=True,
        standard_models=_STANDARD_MODELS,
    )
    assert verdict.admitted


def test_opus_still_requires_cost_intent_when_healthy() -> None:
    verdict = evaluate_anthropic_override(
        model=_OPUS,
        profile_provider="openai",
        profile_allowed_models=(),
    )
    assert not verdict.admitted


def test_fable_still_requires_cost_intent_when_healthy() -> None:
    verdict = evaluate_anthropic_override(
        model=_FABLE,
        profile_provider="openai",
        profile_allowed_models=(),
    )
    assert not verdict.admitted


def test_standard_model_rejects_when_credit_unhealthy() -> None:
    verdict = evaluate_anthropic_override(
        model=_SONNET55,
        profile_provider="openai",
        profile_allowed_models=(),
        credit_healthy=False,
        standard_models=_STANDARD_MODELS,
    )
    assert not verdict.admitted


def test_openrouter_sonnet_not_standard_admitted_without_cost_intent() -> None:
    verdict = evaluate_anthropic_override(
        model=_OPENROUTER_SONNET,
        profile_provider="openai",
        profile_allowed_models=(),
        credit_healthy=True,
        standard_models=_STANDARD_MODELS,
    )
    assert not verdict.admitted


def test_enforce_openrouter_sonnet_raises_422() -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        enforce_anthropic_override(
            request_id="req-openrouter",
            model=_OPENROUTER_SONNET,
            profile_provider="openai",
            profile_allowed_models=(),
            credit_healthy=True,
            standard_models=_STANDARD_MODELS,
        )
    assert exc_info.value.code == REJECT_CODE


def test_opus_5_5_still_requires_cost_intent_when_credit_healthy() -> None:
    verdict = evaluate_anthropic_override(
        model=_OPUS55,
        profile_provider="openai",
        profile_allowed_models=(),
        credit_healthy=True,
    )
    assert not verdict.admitted


@pytest.mark.asyncio
async def test_standard_model_rejects_on_invalid_credit_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(agent: str, **_k: Any) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.6-terra",
                allowed_models=[_SONNET55, "openai/gpt-5.6-terra"],
                allowed_options=None,
            ),
        )

    def _bad_config() -> None:
        from anthropic_credit.config import CreditConfigInvalid

        raise CreditConfigInvalid("broken")

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )
    monkeypatch.setattr(
        "anthropic_credit.config.load_credit_config",
        _bad_config,
    )
    async def _always_healthy() -> bool:
        return True

    monkeypatch.setattr(
        "systems.frontier_consult.service.credit_gate_healthy",
        _always_healthy,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "review this"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model=_SONNET55,
    )

    with pytest.raises(FrontierEndpointError) as exc_info:
        await build_dispatch_body(req)
    assert exc_info.value.code == REJECT_CODE


@pytest.mark.asyncio
async def test_standard_model_rejects_when_credit_probe_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(agent: str, **_k: Any) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.6-terra",
                allowed_models=[_SONNET55, "openai/gpt-5.6-terra"],
                allowed_options=None,
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    async def _boom() -> bool:
        raise RuntimeError("probe failed")

    monkeypatch.setattr(
        "systems.frontier_consult.service.credit_gate_healthy",
        _boom,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "review this"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model=_SONNET55,
    )

    with pytest.raises(FrontierEndpointError) as exc_info:
        await build_dispatch_body(req)
    assert exc_info.value.code == REJECT_CODE


def test_empty_cost_intent_reason_rejects() -> None:
    verdict = evaluate_anthropic_override(
        model=_OPUS,
        profile_provider="openai",
        profile_allowed_models=(),
        cost_intent="deliberate_high_cost",
        cost_intent_reason="   ",
    )
    assert not verdict.admitted


def _bundle(meta: AgentMeta) -> HydrationBundle:
    return HydrationBundle(briefing_card_md="# briefing", agent_meta=meta)


@pytest.mark.asyncio
async def test_build_dispatch_body_rejects_reviewer_opus_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(agent: str, **_k: Any) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.6-terra",
                allowed_models=[_OPUS, "openai/gpt-5.6-terra"],
                allowed_options=None,
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "review this"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model=_OPUS,
    )
    with pytest.raises(FrontierEndpointError) as exc_info:
        await build_dispatch_body(req)
    assert exc_info.value.code == REJECT_CODE


@pytest.mark.asyncio
async def test_build_dispatch_body_admits_reviewer_opus_with_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(agent: str, **_k: Any) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.6-terra",
                allowed_models=[_OPUS, "openai/gpt-5.6-terra"],
                allowed_options=None,
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "review this"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model=_OPUS,
        cost_intent="deliberate_high_cost",
        cost_intent_reason="operator authorized opus review",
    )
    body = await build_dispatch_body(req)
    assert body["pipeline_options"]["model"] == _OPUS


@pytest.mark.asyncio
async def test_credit_gate_healthy_caches_concurrent_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    async def _probe() -> bool:
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return True

    monkeypatch.setattr(
        "systems.frontier_consult.anthropic_override_gate._default_async_credit_health_probe",
        _probe,
    )
    reset_credit_health_cache()
    results = await asyncio.gather(
        credit_gate_healthy(),
        credit_gate_healthy(),
        credit_gate_healthy(),
    )
    assert all(results)
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_credit_gate_unhealthy_reprobed_after_short_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    async def _probe() -> bool:
        calls["n"] += 1
        return False

    monkeypatch.setattr(
        "systems.frontier_consult.anthropic_override_gate._default_async_credit_health_probe",
        _probe,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.anthropic_override_gate._UNHEALTHY_TTL_S",
        0.05,
    )
    reset_credit_health_cache()
    assert await credit_gate_healthy() is False
    await asyncio.sleep(0.06)
    assert await credit_gate_healthy() is False
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_default_credit_health_probe_mock_transport_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.frontier_consult import anthropic_override_gate as gate_mod

    class _Resp:
        def __init__(self, status_code: int, payload: object) -> None:
            self.status_code = status_code
            self._payload = payload

        def json(self) -> object:
            if isinstance(self._payload, Exception):
                raise self._payload
            return self._payload

    class _Client:
        def __init__(self, status: int, payload: object) -> None:
            self._status = status
            self._payload = payload

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        async def get(self, _path: str) -> _Resp:
            return _Resp(self._status, self._payload)

    def _factory(status: int, payload: object):
        def _fake_client(_url: str, timeout: httpx.Timeout) -> _Client:
            return _Client(status, payload)

        return _fake_client

    unhealthy_cases = [
        (503, {"healthy": True}),
        (200, "not-json"),
        (200, {"healthy": "true"}),
        (200, {}),
    ]
    for status, payload in unhealthy_cases:
        monkeypatch.setattr(
            "transport_utils.make_async_client",
            _factory(status, payload),
        )
        assert await gate_mod._default_async_credit_health_probe() is False

    monkeypatch.setattr(
        "transport_utils.make_async_client",
        _factory(200, {"healthy": True}),
    )
    assert await gate_mod._default_async_credit_health_probe() is True


@pytest.mark.asyncio
async def test_slow_credit_probe_rejects_standard_model_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket
    import threading

    from systems.frontier_consult import anthropic_override_gate as gate_mod

    async def fake_hydrate(agent: str, **_k: Any) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.6-terra",
                allowed_models=[_SONNET55, "openai/gpt-5.6-terra"],
                allowed_options=None,
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    stall_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    stall_sock.bind(("127.0.0.1", 0))
    stall_sock.listen(5)
    port = stall_sock.getsockname()[1]

    def _accept_and_stall() -> None:
        while True:
            try:
                conn, _addr = stall_sock.accept()
            except OSError:
                break
            try:
                threading.Event().wait(30.0)
            finally:
                conn.close()

    threading.Thread(target=_accept_and_stall, daemon=True).start()
    monkeypatch.setattr(
        gate_mod,
        "_configured_cloud_proxy_url",
        lambda: f"http://127.0.0.1:{port}",
    )
    reset_configured_cloud_proxy_url_cache()
    reset_credit_health_cache()

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "review this"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model=_SONNET55,
    )
    import time

    started = time.monotonic()
    with pytest.raises(FrontierEndpointError) as exc_info:
        await build_dispatch_body(req)
    elapsed = time.monotonic() - started
    assert exc_info.value.status_code == 422
    assert elapsed < 2.5
    stall_sock.close()


@pytest.mark.asyncio
def test_configured_cloud_proxy_url_not_cached_when_stargate_config_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.frontier_consult import anthropic_override_gate as gate_mod

    reset_configured_cloud_proxy_url_cache()
    calls = {"n": 0}

    def _boom() -> None:
        calls["n"] += 1
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(
        "systems.proxy.stargate_config.config.StargateConfig",
        _boom,
    )
    first = gate_mod._configured_cloud_proxy_url()
    second = gate_mod._configured_cloud_proxy_url()
    assert calls["n"] == 2
    assert first == second


@pytest.mark.asyncio
async def test_cloud_proxy_url_uses_uds_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    import threading
    import time

    import uvicorn
    from fastapi import FastAPI
    from transport_utils.client_factory import make_async_client, parse_rag_url

    from systems.frontier_consult import anthropic_override_gate as gate_mod

    sock_path = tmp_path / "cloud-proxy.sock"
    probe_app = FastAPI()

    @probe_app.get("/internal/anthropic-credit/status")
    def _status() -> dict[str, bool]:
        return {"healthy": True}

    def _serve_uds() -> None:
        uvicorn.run(
            probe_app,
            uds=str(sock_path),
            log_level="error",
            access_log=False,
        )

    thread = threading.Thread(target=_serve_uds, daemon=True)
    thread.start()
    for _ in range(50):
        if sock_path.exists():
            break
        time.sleep(0.02)

    url = f"unix://{sock_path}"
    monkeypatch.setattr(gate_mod, "_configured_cloud_proxy_url", lambda: url)
    reset_configured_cloud_proxy_url_cache()
    reset_credit_health_cache()

    captured: dict[str, Any] = {}
    real_make = make_async_client

    def _capture_make(
        target_url: str, timeout: httpx.Timeout | float = 10.0, **kw: Any
    ):
        client = real_make(target_url, timeout=timeout, **kw)
        captured["transport"] = client._transport
        captured["url"] = target_url
        return client

    monkeypatch.setattr("transport_utils.make_async_client", _capture_make)

    assert await gate_mod._default_async_credit_health_probe() is True
    assert captured["url"] == url
    uds_path, _ = parse_rag_url(captured["url"])
    assert uds_path == str(sock_path)
    assert isinstance(captured["transport"], httpx.AsyncHTTPTransport)


@pytest.mark.asyncio
async def test_slow_credit_probe_does_not_block_other_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    progress = {"n": 0}

    async def _slow_probe() -> bool:
        await asyncio.sleep(0.3)
        return False

    async def _tick() -> None:
        await asyncio.sleep(0.05)
        progress["n"] += 1

    monkeypatch.setattr(
        "systems.frontier_consult.anthropic_override_gate._default_async_credit_health_probe",
        _slow_probe,
    )
    reset_credit_health_cache()
    probe_task = asyncio.create_task(credit_gate_healthy())
    tick_task = asyncio.create_task(_tick())
    await asyncio.gather(probe_task, tick_task)
    assert progress["n"] >= 1


