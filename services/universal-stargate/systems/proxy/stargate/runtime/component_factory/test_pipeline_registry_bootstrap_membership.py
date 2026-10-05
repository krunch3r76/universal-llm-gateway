"""Membership tag captured at reload start (friction 37827)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from systems.proxy.stargate.runtime.component_factory import (
    pipeline_registry_bootstrap as bootstrap,
)


def _gateway(
    gateway_id: str, *, unreachable: bool, models: frozenset[str]
) -> SimpleNamespace:
    return SimpleNamespace(
        gateway_id=gateway_id,
        is_unreachable=unreachable,
        available_models=models,
    )


def test_membership_payload_includes_catalog_gateway_ids_reachable_non_empty_only():
    gateways = [
        _gateway("a", unreachable=False, models=frozenset({"m1", "m2", "m3"})),
        _gateway("b", unreachable=False, models=frozenset()),
        _gateway("c", unreachable=True, models=frozenset({"m1", "m2", "m3"})),
    ]
    proxy = SimpleNamespace(
        federated_manager=SimpleNamespace(get_all_gateways=lambda: gateways),
        pipeline_registry=SimpleNamespace(pipelines={}),
    )
    payload = bootstrap.membership_payload(proxy)
    assert payload["gateway_ids"] == ["a", "b"]
    assert payload["catalog_gateway_ids"] == ["a"]


@pytest.mark.asyncio
async def test_reload_after_federation_event_tags_membership_with_catalog_view_at_reload_start():  # noqa: E501
    gateways = [_gateway("a", unreachable=False, models=frozenset({"m"}))]
    published: list = []

    class _Bus:
        async def publish_nowait(self, event) -> None:
            published.append(event)

    registry = SimpleNamespace(pipelines={}, unavailable_pipelines=[])

    def reload_pipelines():
        if not any(gateway.gateway_id == "d" for gateway in gateways):
            gateways.append(_gateway("d", unreachable=False, models=frozenset({"m"})))
        registry.pipelines["p"] = object()
        return (0, 1)

    registry.reload_pipelines = reload_pipelines
    proxy = SimpleNamespace(
        federated_manager=SimpleNamespace(get_all_gateways=lambda: list(gateways)),
        pipeline_registry=registry,
        event_bus=_Bus(),
        pipeline_catalog_synced=False,
    )
    event = SimpleNamespace(payload={"gateway_id": "a"})
    await bootstrap._reload_pipelines_after_federation_event(
        proxy, event, reason="catalog change"
    )
    assert "d" not in published[0].payload["catalog_gateway_ids"]
    await bootstrap._reload_pipelines_after_federation_event(
        proxy, event, reason="catalog change"
    )
    assert "d" in published[1].payload["catalog_gateway_ids"]


@pytest.mark.asyncio
async def test_init_membership_without_reload_is_tagged(
    monkeypatch: pytest.MonkeyPatch,
):
    gateways = [_gateway("a", unreachable=False, models=frozenset({"m"}))]
    published: list = []
    reloads: list[str] = []

    class _Bus:
        async def publish_nowait(self, event) -> None:
            published.append(event)

    class _Registry:
        def __init__(self, *args, **kwargs) -> None:
            self.pipelines: dict = {}
            self.unavailable_pipelines: list = []

        def load(self) -> None:
            return None

        def reload_pipelines(self):
            reloads.append("reload")
            return (0, 0)

    class _Executor:
        def __init__(self, *args, **kwargs) -> None:
            return None

    class _Tracker:
        def __init__(self, *args, **kwargs) -> None:
            return None

    monkeypatch.setattr("systems.pipeline.registry.PipelineRegistry", _Registry)
    monkeypatch.setattr("systems.pipeline.executor.PipelineExecutor", _Executor)
    monkeypatch.setattr(
        "systems.pipeline.core.execution.async_tracker.PipelineExecutionTracker",
        _Tracker,
    )
    monkeypatch.setattr(
        "systems.pipeline.user_handlers.load_user_handlers",
        lambda **kwargs: 0,
    )
    proxy = SimpleNamespace(
        config=SimpleNamespace(
            get_pipelines_config=lambda: {
                "search_paths": [],
                "defaults": {},
                "hot_reload": {"enabled": False},
            },
            config_path=None,
        ),
        gateway_manager=None,
        federation_integration=None,
        federated_manager=SimpleNamespace(get_all_gateways=lambda: gateways),
        event_bus=_Bus(),
        request_executor=object(),
        pipeline_registry=None,
        pipeline_executor=None,
    )
    await bootstrap.initialize_pipeline_system(proxy)
    memberships = [
        event.payload
        for event in published
        if getattr(event, "signal", "") == "federation.gateway.membership"
    ]
    assert memberships
    assert memberships[0]["catalog_gateway_ids"] == bootstrap.catalog_gateway_ids_now(
        proxy
    )
    assert reloads == []
