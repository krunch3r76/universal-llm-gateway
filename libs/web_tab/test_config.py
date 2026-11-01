"""CDP URL resolution and allowlist."""

from __future__ import annotations

import pytest

from web_tab.config import resolve_cdp_url


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
