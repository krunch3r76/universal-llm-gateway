"""Probe JS sign-in is unknown when neither composer nor login wall is seen."""

from __future__ import annotations

from web_tab.surface_adapter import (
    CHATGPT_PROBE_JS,
    CLAUDE_PROBE_JS,
    canonical_page_url,
    signed_in_from_signals,
    url_in_surface_scope,
)


def test_no_composer_no_wall_is_unknown() -> None:
    """Neither composer nor login wall yields unknown signed_in in the probe JS."""
    assert signed_in_from_signals(login_wall=False, composer=False) is None
    assert signed_in_from_signals(login_wall=True, composer=False) is False
    assert signed_in_from_signals(login_wall=False, composer=True) is True
    assert "loginWall ? false : (composer ? true : null)" in CHATGPT_PROBE_JS


def test_host_path_gate_refuses_other_surfaces() -> None:
    """Chat paths on chatgpt.com pass; account hosts and settings paths do not."""
    assert url_in_surface_scope("chatgpt", "https://chatgpt.com/c/abc")
    assert url_in_surface_scope("chatgpt", "https://chatgpt.com/")
    assert not url_in_surface_scope("chatgpt", "https://accounts.google.com/signin")
    assert not url_in_surface_scope("chatgpt", "https://auth.openai.com/login")
    assert not url_in_surface_scope("chatgpt", "https://chatgpt.com/settings")


def test_claude_surface_host_gate() -> None:
    """Claude hosts pass for claude; console host for claude-console; cross-refuse."""
    assert url_in_surface_scope("claude", "https://claude.ai/")
    assert url_in_surface_scope("claude", "https://www.claude.ai/chat/abc")
    assert url_in_surface_scope("claude-console", "https://console.anthropic.com/settings")
    assert url_in_surface_scope(
        "claude-console", "https://platform.claude.com/settings/keys"
    )
    assert url_in_surface_scope("claude-console", "https://www.platform.claude.com/")
    assert not url_in_surface_scope("claude", "https://platform.claude.com/")
    assert not url_in_surface_scope("claude-console", "https://claude.ai/new")
    assert not url_in_surface_scope(
        "claude-console", "https://platform.claude.com.evil.example/"
    )
    assert not url_in_surface_scope(
        "claude-console", "https://evilplatform.claude.com/"
    )
    assert not url_in_surface_scope("claude", "https://chatgpt.com/")
    assert not url_in_surface_scope("chatgpt", "https://claude.ai/")
    assert not url_in_surface_scope("chatgpt", "https://accounts.google.com/signin")
    assert not url_in_surface_scope("claude", "https://accounts.google.com/signin")
    assert not url_in_surface_scope("claude-console", "https://accounts.google.com/signin")
    assert "loginWall ? false : null" in CLAUDE_PROBE_JS


def test_claude_surface_scheme_port_and_host_hardening() -> None:
    """HTTPS default port only; exact host match; userinfo and lookalike hosts refused."""
    assert url_in_surface_scope("claude", "https://CLAUDE.AI/new")
    assert url_in_surface_scope(
        "claude-console", "https://platform.claude.com:443/settings"
    )
    assert not url_in_surface_scope("claude", "http://claude.ai/")
    assert not url_in_surface_scope("claude", "https://claude.ai:8080/")
    assert not url_in_surface_scope(
        "claude-console", "https://platform.claude.com@evil.example/"
    )
    assert not url_in_surface_scope("claude", "https://evilclaude.ai/")


def test_chatgpt_surface_scheme_port_hardening() -> None:
    """ChatGPT paths refuse cleartext and non-default HTTPS ports."""
    assert not url_in_surface_scope("chatgpt", "http://chatgpt.com/c/abc")
    assert not url_in_surface_scope("chatgpt", "https://chatgpt.com:8080/")


def test_malformed_port_tab_does_not_break_scope_probe() -> None:
    """Invalid port strings are out of scope without raising."""
    assert not url_in_surface_scope("claude", "https://claude.ai:abc/")
    assert not url_in_surface_scope("claude", "https://claude.ai:99999/")
    urls = [
        "https://claude.ai/",
        "https://claude.ai:abc/",
        "https://claude.ai:8080/",
    ]
    assert all(
        isinstance(url_in_surface_scope("claude", u), bool) for u in urls
    )


def test_canonical_page_url_strips_https_default_port() -> None:
    assert canonical_page_url("https://claude.ai:443/x") == canonical_page_url(
        "https://claude.ai/x"
    )


def test_canonical_page_url_never_raises_on_bad_port() -> None:
    assert canonical_page_url("https://claude.ai:abc/path") == "https://claude.ai/path"
