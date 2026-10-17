"""A0 — static proof that Anthropic egress chains through the gated cloud-proxy adapter."""

from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.offline


def test_provider_forwarder_forward_native_delegates_to_adapter() -> None:
    """Break covered: native route bypasses adapter.forward_native."""
    from services.universal_cloud_proxy.forwarder import ProviderForwarder

    source = inspect.getsource(ProviderForwarder.forward_native)
    assert "forward_native" in source
    assert "_adapter(provider)" in source or "self._adapter" in source


def test_llm_proxy_targets_stargate_providers_anthropic_messages() -> None:
    """Break covered: llm_proxy drifts to direct api.anthropic.com."""
    from pathlib import Path

    text = (
        Path(__file__).resolve().parents[1]
        / "mcp-server"
        / "llm_proxy.py"
    ).read_text(encoding="utf-8")
    assert (
        '_PROVIDERS_ANTHROPIC_MESSAGES = "/api/v1/providers/anthropic/messages"'
        in text
    )


def test_stargate_providers_native_passthrough_to_cloud_proxy_anthropic_messages() -> None:
    """Break covered: Stargate native router stops relaying to cloud-proxy path."""
    from systems.proxy.routers.api import providers_native

    assert providers_native._VALID_ROUTES[("anthropic", "messages")] == (
        "/api/v1/providers/anthropic/messages"
    )


def test_native_forwarders_forward_native_uses_forwarder() -> None:
    """Break covered: native_routes handler skips ProviderForwarder."""
    from services.universal_cloud_proxy import native_forwarders

    source = inspect.getsource(native_forwarders.forward_native)
    assert "forwarder.forward_native" in source


def test_workbench_relay_targets_same_providers_native_path() -> None:
    """Break covered: workbench_relay drifts off providers-native passthrough."""
    from pathlib import Path

    text = (
        Path(__file__).resolve().parents[1]
        / "mcp-server"
        / "workbench_relay.py"
    ).read_text(encoding="utf-8")
    assert (
        '_PROVIDERS_ANTHROPIC_MESSAGES = "/api/v1/providers/anthropic/messages"'
        in text
    )
