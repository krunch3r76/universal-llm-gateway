"""Surface URL scope gates."""

from __future__ import annotations

from web_tab.surface_adapter import (
    canonical_page_url,
    is_claude_ai_chat_url,
    surface_not_web_tab_owned_refusal,
    url_in_surface_scope,
)


def test_chatgpt_surface_host_gate() -> None:
    """Chat paths on chatgpt.com pass; account hosts and settings paths do not."""
    assert url_in_surface_scope("chatgpt", "https://chatgpt.com/c/abc")
    assert url_in_surface_scope("chatgpt", "https://chatgpt.com/")
    assert not url_in_surface_scope("chatgpt", "https://accounts.google.com/signin")
    assert not url_in_surface_scope("chatgpt", "https://auth.openai.com/login")
    assert not url_in_surface_scope("chatgpt", "https://chatgpt.com/settings")


def test_anthropic_console_surface_host_gate() -> None:
    """Console hosts pass; claude.ai chat never passes (cdp_ask-owned)."""
    assert url_in_surface_scope(
        "anthropic_console", "https://console.anthropic.com/settings"
    )
    assert url_in_surface_scope(
        "anthropic_console", "https://platform.claude.com/settings/keys"
    )
    assert url_in_surface_scope(
        "anthropic_console", "https://www.platform.claude.com/"
    )
    assert not url_in_surface_scope("anthropic_console", "https://claude.ai/new")
    assert not url_in_surface_scope("claude", "https://claude.ai/chat/abc")
    assert not url_in_surface_scope(
        "anthropic_console", "https://platform.claude.com.evil.example/"
    )
    assert not url_in_surface_scope(
        "anthropic_console", "https://evilplatform.claude.com/"
    )
    assert not url_in_surface_scope("anthropic_console", "https://chatgpt.com/")
    assert not url_in_surface_scope("chatgpt", "https://claude.ai/")
    assert not url_in_surface_scope("chatgpt", "https://accounts.google.com/signin")
    assert not url_in_surface_scope("claude", "https://accounts.google.com/signin")
    assert not url_in_surface_scope(
        "claude-console", "https://accounts.google.com/signin"
    )


def test_claude_surface_alias_maps_to_console_not_chat() -> None:
    """Legacy surface `claude` is console-only back-compat, never claude.ai chat."""
    assert url_in_surface_scope("claude", "https://console.anthropic.com/sign-in")
    assert not url_in_surface_scope("claude", "https://www.claude.ai/chat/abc")


def test_anthropic_console_scheme_port_and_host_hardening() -> None:
    assert url_in_surface_scope(
        "anthropic_console", "https://CONSOLE.ANTHROPIC.COM/sign-in"
    )
    assert url_in_surface_scope(
        "anthropic_console", "https://platform.claude.com:443/settings"
    )
    assert not url_in_surface_scope("claude", "http://claude.ai/")
    assert not url_in_surface_scope("claude", "https://claude.ai:8080/")
    assert not url_in_surface_scope(
        "anthropic_console", "https://platform.claude.com@evil.example/"
    )
    assert not url_in_surface_scope("claude", "https://evilclaude.ai/")


def test_chatgpt_surface_scheme_port_hardening() -> None:
    assert not url_in_surface_scope("chatgpt", "http://chatgpt.com/c/abc")
    assert not url_in_surface_scope("chatgpt", "https://chatgpt.com:8080/")


def test_claude_ai_chat_refusal_points_to_cdp_ask() -> None:
    assert is_claude_ai_chat_url("https://claude.ai/chat/x")
    refusal = surface_not_web_tab_owned_refusal("https://www.claude.ai/new")
    assert refusal is not None
    assert refusal["code"] == "surface_not_web_tab_owned"
    assert refusal["owner"] == "cdp_ask"


def test_malformed_claude_urls_do_not_crash_scope() -> None:
    assert not url_in_surface_scope("claude", "https://claude.ai:abc/")
    assert not url_in_surface_scope("claude", "https://claude.ai:99999/")
    urls = (
        "https://claude.ai/",
        "https://claude.ai:abc/",
        "https://claude.ai:8080/",
    )
    assert all(isinstance(url_in_surface_scope("claude", u), bool) for u in urls)


def test_canonical_page_url_strips_default_port_and_case() -> None:
    assert canonical_page_url("https://claude.ai:443/x") == canonical_page_url(
        "https://claude.ai/x"
    )
    assert canonical_page_url("https://claude.ai:abc/path") == "https://claude.ai/path"


def test_malformed_bracket_urls_refused() -> None:
    for url in (
        "https://[claude.ai/",
        "https://claude.ai]/",
    ):
        assert not url_in_surface_scope("claude", url)
