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
_GROK = "xai/grok-4.5"


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
