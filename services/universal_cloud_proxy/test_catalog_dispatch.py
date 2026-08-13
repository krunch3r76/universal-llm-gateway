"""A2 — cloud_proxy /catalog dispatch projection (G11).

Asserts the cloud catalog carries the libs ``dispatch`` wire facet, that the
synthetic variant rows (``-mcp``, xai ``-effort-*``) inherit it through the
``to_dict()`` spread, and that a provider outside the dispatch surface map omits
``dispatch`` AND fires the ``on_dispatch_catalog_miss`` hook (no invented
default).
"""

from __future__ import annotations

from typing import Any

import pytest
from llm_adapters.capability_dispatch import resolve, to_wire_dict

from services.universal_cloud_proxy.catalog import (
    CatalogManager,
    CatalogModel,
    ProviderCatalog,
)
from services.universal_cloud_proxy.config import ProviderConfig
from services.universal_cloud_proxy.native_boundary import (
    _EFFORT_LEVELS,
    _EFFORT_SUFFIX,
)

_OPUS = "anthropic/claude-opus-4-8"
_GROK = "xai/grok-4.6"


def _manager_with(provider: str, model: CatalogModel) -> CatalogManager:
    mgr = CatalogManager([], {})
    mgr._catalogs[provider] = ProviderCatalog(provider=provider, models=[model])  # noqa: SLF001
    return mgr


def test_to_dict_includes_dispatch_when_set() -> None:
    wire = to_wire_dict(resolve(_OPUS))
    model = CatalogModel(
        id=_OPUS, provider="anthropic", max_concurrent=5, dispatch=wire
    )
    assert model.to_dict()["dispatch"] == wire


def test_to_dict_omits_dispatch_when_none() -> None:
    model = CatalogModel(id=_OPUS, provider="anthropic", max_concurrent=5)
    assert "dispatch" not in model.to_dict()


def test_mcp_variant_inherits_dispatch() -> None:
    wire = to_wire_dict(resolve(_OPUS))
    mgr = _manager_with(
        "anthropic",
        CatalogModel(id=_OPUS, provider="anthropic", max_concurrent=5, dispatch=wire),
    )
    rows = {r["id"]: r for r in mgr.get_all_models()}
    assert rows[_OPUS]["dispatch"] == wire
    assert rows[f"{_OPUS}-mcp"]["dispatch"] == wire


def test_xai_effort_variants_inherit_dispatch() -> None:
    wire = to_wire_dict(resolve(_GROK))
    mgr = _manager_with(
        "xai",
        CatalogModel(id=_GROK, provider="xai", max_concurrent=5, dispatch=wire),
    )
    rows = {r["id"]: r for r in mgr.get_all_models()}
    for level in _EFFORT_LEVELS:
        assert rows[f"{_GROK}{_EFFORT_SUFFIX}{level}"]["dispatch"] == wire
    assert rows[f"{_GROK}-mcp"]["dispatch"] == wire


class _FakeAdapter:
    """Minimal ProviderAdapter stand-in for the catalog fetch path."""

    def __init__(self, provider: str, entries: list[dict[str, Any]]) -> None:
        self._provider = provider
        self._entries = entries

    @property
    def adapter_type(self) -> str:
        return "fake"

    def normalize_catalog_model_id(self, raw_model_id: str) -> str:
        if "/" in raw_model_id:
            return raw_model_id
        return f"{self._provider}/{raw_model_id}"

    async def fetch_catalog(self) -> list[dict[str, Any]]:
        return self._entries


@pytest.mark.asyncio
async def test_fetch_provider_populates_dispatch_in_surface() -> None:
    cfg = ProviderConfig(provider="anthropic", api_key="x")
    adapter = _FakeAdapter("anthropic", [{"id": "claude-opus-4-8"}])
    mgr = CatalogManager([cfg], {"anthropic": adapter})

    assert await mgr._fetch_provider(cfg)  # noqa: SLF001
    models = mgr._catalogs["anthropic"].models  # noqa: SLF001
    assert len(models) == 1
    assert models[0].dispatch == to_wire_dict(resolve(_OPUS))


@pytest.mark.asyncio
async def test_fetch_provider_catalog_miss_omits_dispatch_and_emits() -> None:
    misses: list[tuple[str, str, str]] = []

    async def _on_miss(provider: str, model_id: str, reason: str) -> None:
        misses.append((provider, model_id, reason))

    cfg = ProviderConfig(provider="frobnozz", api_key="x")
    adapter = _FakeAdapter("frobnozz", [{"id": "some-model"}])
    mgr = CatalogManager(
        [cfg], {"frobnozz": adapter}, on_dispatch_catalog_miss=_on_miss
    )

    assert await mgr._fetch_provider(cfg)  # noqa: SLF001
    model = mgr._catalogs["frobnozz"].models[0]  # noqa: SLF001
    assert model.dispatch is None
    assert "dispatch" not in model.to_dict()
    assert len(misses) == 1
    assert misses[0][0] == "frobnozz"
    assert misses[0][1] == "frobnozz/some-model"


@pytest.mark.asyncio
async def test_fetch_provider_list_pricing_and_non_dict_entries() -> None:
    """xAI-style list ``pricing`` must not abort catalog ingest (process crash)."""
    cfg = ProviderConfig(provider="xai", api_key="x")
    adapter = _FakeAdapter(
        "xai",
        [
            {"id": "grok-4.6", "pricing": [{"type": "input", "cost": "0.001"}]},
            {
                "id": "grok-4",
                "pricing": {"prompt": "0.000002", "completion": "0.00001"},
            },
            "not-a-dict",
        ],
    )
    mgr = CatalogManager([cfg], {"xai": adapter})

    assert await mgr._fetch_provider(cfg)  # noqa: SLF001
    models = {m.id: m for m in mgr._catalogs["xai"].models}  # noqa: SLF001
    assert set(models) == {"xai/grok-4.6", "xai/grok-4"}
    assert models["xai/grok-4.6"].prompt_cost_per_m == 0.0
    assert models["xai/grok-4"].prompt_cost_per_m == 2.0


class _BoomAdapter(_FakeAdapter):
    async def fetch_catalog(self) -> list[dict[str, Any]]:
        raise RuntimeError("catalog shape boom")


@pytest.mark.asyncio
async def test_startup_continues_when_one_provider_raises() -> None:
    failures: list[tuple[str, str]] = []

    async def _on_fail(provider: str, error: str) -> None:
        failures.append((provider, error))

    good_cfg = ProviderConfig(provider="anthropic", api_key="x")
    bad_cfg = ProviderConfig(provider="xai", api_key="x")
    mgr = CatalogManager(
        [good_cfg, bad_cfg],
        {
            "anthropic": _FakeAdapter("anthropic", [{"id": "claude-opus-4-8"}]),
            "xai": _BoomAdapter("xai", []),
        },
        on_provider_catalog_refresh_failed=_on_fail,
    )

    await mgr.startup()
    try:
        assert "anthropic" in mgr._catalogs  # noqa: SLF001
        assert mgr._catalogs["anthropic"].models[0].id == _OPUS  # noqa: SLF001
        assert "xai" not in mgr._catalogs  # noqa: SLF001
        assert len(failures) == 1
        assert failures[0][0] == "xai"
        assert "catalog shape boom" in failures[0][1]
    finally:
        await mgr.shutdown()


class _CountingAdapter(_FakeAdapter):
    def __init__(self, provider: str, entries: list[dict[str, Any]]) -> None:
        super().__init__(provider, entries)
        self.fetches = 0

    async def fetch_catalog(self) -> list[dict[str, Any]]:
        self.fetches += 1
        return await super().fetch_catalog()


class _SucceedThenBoomAdapter(_FakeAdapter):
    def __init__(self, provider: str, entries: list[dict[str, Any]]) -> None:
        super().__init__(provider, entries)
        self.fetches = 0

    async def fetch_catalog(self) -> list[dict[str, Any]]:
        self.fetches += 1
        if self.fetches > 1:
            raise RuntimeError("second fetch boom")
        return self._entries


@pytest.mark.asyncio
async def test_refresh_refetches_providers() -> None:
    adapter = _CountingAdapter("anthropic", [{"id": "claude-opus-4-8"}])
    cfg = ProviderConfig(provider="anthropic", api_key="x")
    mgr = CatalogManager([cfg], {"anthropic": adapter})
    await mgr.startup()
    assert adapter.fetches == 1
    counts = await mgr.refresh()
    assert adapter.fetches == 2
    assert counts == {"anthropic": 1}
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_refresh_keeps_prior_cache_when_provider_raises() -> None:
    failures: list[tuple[str, str]] = []

    async def _on_fail(provider: str, error: str) -> None:
        failures.append((provider, error))

    adapter = _SucceedThenBoomAdapter("xai", [{"id": "grok-4.6"}])
    cfg = ProviderConfig(provider="xai", api_key="x")
    mgr = CatalogManager(
        [cfg], {"xai": adapter}, on_provider_catalog_refresh_failed=_on_fail
    )
    await mgr.startup()
    assert mgr._catalogs["xai"].models[0].id == _GROK  # noqa: SLF001
    counts = await mgr.refresh()
    assert counts == {"xai": 1}
    assert mgr._catalogs["xai"].models[0].id == _GROK  # noqa: SLF001
    assert len(failures) == 1
    assert failures[0][0] == "xai"
    assert "second fetch boom" in failures[0][1]
    await mgr.shutdown()
