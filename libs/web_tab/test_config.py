"""CDP URL resolution and allowlist."""

from __future__ import annotations

import pytest

from web_tab.config import (
    WEB_TAB_SMOKE_PROVIDER_FIXTURES,
    load_web_tab_houses,
    normalize_web_tab_surface,
    resolve_cdp_url,
    resolve_cdp_url_for_surface,
)


def test_resolve_cdp_url_accepts_default_provision_ports() -> None:
    assert resolve_cdp_url("http://127.0.0.1:9342") == "http://127.0.0.1:9342"
    assert resolve_cdp_url("http://localhost:9343") == "http://localhost:9343"
    assert resolve_cdp_url("http://[::1]:9342") == "http://[::1]:9342"


def test_resolve_cdp_url_from_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_TAB_CDP_URL", "http://127.0.0.1:9343")
    assert resolve_cdp_url(None) == "http://127.0.0.1:9343"


def test_resolve_cdp_url_rejects_file_scheme() -> None:
    with pytest.raises(ValueError, match="invalid_cdp_url:scheme:file"):
        resolve_cdp_url("file:///etc/passwd")


def test_resolve_cdp_url_rejects_non_loopback() -> None:
    with pytest.raises(ValueError, match="invalid_cdp_url:host:example.com"):
        resolve_cdp_url("http://example.com:9342")


def test_resolve_cdp_url_rejects_non_allowlisted_port() -> None:
    with pytest.raises(ValueError, match="invalid_cdp_url:port:9222"):
        resolve_cdp_url("http://127.0.0.1:9222")


def test_resolve_cdp_url_env_port_extends_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEB_TAB_CDP_URL", "http://127.0.0.1:9225")
    assert resolve_cdp_url("http://127.0.0.1:9225") == "http://127.0.0.1:9225"


def test_resolve_cdp_url_strips_userinfo_and_path_on_loopback() -> None:
    assert resolve_cdp_url("http://x@127.0.0.1:9343/path") == "http://127.0.0.1:9343"


def test_resolve_cdp_url_rejects_userinfo_non_loopback() -> None:
    with pytest.raises(ValueError, match="invalid_cdp_url:host:"):
        resolve_cdp_url("http://user@example.com:9342")


def test_resolve_cdp_url_rejects_localhost_dot_suffix() -> None:
    with pytest.raises(ValueError, match="invalid_cdp_url:host:localhost."):
        resolve_cdp_url("http://localhost.:9343")


def test_resolve_cdp_url_rejects_missing_port() -> None:
    with pytest.raises(ValueError, match="invalid_cdp_url:port:missing"):
        resolve_cdp_url("http://127.0.0.1/path")


def test_resolve_cdp_url_rejects_malformed_port() -> None:
    with pytest.raises(ValueError, match="invalid_cdp_url:port:malformed"):
        resolve_cdp_url("http://127.0.0.1:abc")


def test_resolve_cdp_url_rejects_ws_scheme() -> None:
    with pytest.raises(ValueError, match="invalid_cdp_url:scheme:ws"):
        resolve_cdp_url("ws://127.0.0.1:9343")


def test_resolve_cdp_url_rejects_expanded_ipv6_loopback() -> None:
    with pytest.raises(ValueError, match="invalid_cdp_url:host:"):
        resolve_cdp_url("http://[0:0:0:0:0:0:0:1]:9343")


def test_resolve_cdp_url_rejects_malformed_brackets() -> None:
    with pytest.raises(ValueError, match="invalid_cdp_url:malformed"):
        resolve_cdp_url("http://[")


def test_resolve_cdp_url_malformed_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_TAB_CDP_URL", "http://127.0.0.1:9343")
    with pytest.raises(ValueError, match="invalid_cdp_url:malformed"):
        resolve_cdp_url("http://[")


def test_houses_yaml_maps_chatgpt_and_anthropic_console() -> None:
    houses = load_web_tab_houses()
    assert houses["chatgpt"].cdp_url == "http://127.0.0.1:9342"
    assert houses["chatgpt"].display == ":12"
    assert houses["anthropic_console"].cdp_url == "http://127.0.0.1:9343"
    assert houses["anthropic_console"].display == ":13"
    assert "grok" not in houses
    assert "claude" not in houses


def test_normalize_web_tab_surface_claude_alias_is_console() -> None:
    assert normalize_web_tab_surface("claude") == "anthropic_console"
    assert normalize_web_tab_surface("anthropic_console") == "anthropic_console"


def test_smoke_fixture_gate_excludes_claude_chat() -> None:
    assert "claude_chat.html" not in WEB_TAB_SMOKE_PROVIDER_FIXTURES
    assert "chatgpt_composer.html" in WEB_TAB_SMOKE_PROVIDER_FIXTURES
    assert "grok_chat.html" in WEB_TAB_SMOKE_PROVIDER_FIXTURES


def test_resolve_cdp_url_for_surface_defaults_by_surface() -> None:
    assert (
        resolve_cdp_url_for_surface("chatgpt", None) == "http://127.0.0.1:9342"
    )
    assert (
        resolve_cdp_url_for_surface("anthropic_console", None)
        == "http://127.0.0.1:9343"
    )
    assert resolve_cdp_url_for_surface("claude", None) == "http://127.0.0.1:9343"
    assert resolve_cdp_url_for_surface("openai", None) == "http://127.0.0.1:9342"


def test_resolve_cdp_url_for_surface_mismatch_refuses_wrong_house() -> None:
    with pytest.raises(ValueError, match="cdp_surface_mismatch"):
        resolve_cdp_url_for_surface("anthropic_console", "http://127.0.0.1:9342")


def test_resolve_cdp_url_for_surface_grok_requires_explicit_or_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WEB_TAB_CDP_URL", raising=False)
    with pytest.raises(ValueError, match="missing_cdp_url"):
        resolve_cdp_url_for_surface("grok", None)
    monkeypatch.setenv("WEB_TAB_CDP_URL", "http://127.0.0.1:9225")
    assert resolve_cdp_url_for_surface("grok", None) == "http://127.0.0.1:9225"


def test_resolve_cdp_url_for_surface_env_override_per_house(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEB_TAB_CDP_URL_CHATGPT", "http://127.0.0.1:9342")
    assert (
        resolve_cdp_url_for_surface("chatgpt", None) == "http://127.0.0.1:9342"
    )
    monkeypatch.setenv("WEB_TAB_CDP_URL_CHATGPT", "http://127.0.0.1:9225")
    assert resolve_cdp_url_for_surface("chatgpt", None) == "http://127.0.0.1:9225"


def test_resolve_cdp_url_host_error_truncates_long_host() -> None:
    long_host = "a" * 100 + ".example.com"
    with pytest.raises(ValueError) as exc:
        resolve_cdp_url(f"http://{long_host}:9342")
    msg = str(exc.value)
    assert msg.startswith("invalid_cdp_url:host:")
    assert len(msg.split(":", maxsplit=2)[-1]) == 64
