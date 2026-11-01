"""Private configuration for web_tab observation (not generic persona defaults)."""

from __future__ import annotations

import math
import os
from urllib.parse import SplitResult, urlsplit

_DEFAULT_CDP_EVALUATE_TIMEOUT_S = 10.0
_DEFAULT_CDP_CLEANUP_TIMEOUT_S = 1.0
_DEFAULT_CDP_PORTS = frozenset({9342, 9343})
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_HOST_ERROR_MAX_LEN = 64


def _safe_urlsplit(url: str) -> SplitResult | None:
    """Parse *url* without raising on malformed IPv6/bracket forms."""
    try:
        return urlsplit(url)
    except ValueError:
        return None


def _host_error_suffix(host: str) -> str:
    """Host fragment for errors; truncate long caller-controlled strings."""
    if not host:
        return "missing"
    if len(host) > _HOST_ERROR_MAX_LEN:
        return host[:_HOST_ERROR_MAX_LEN]
    return host


def _allowed_cdp_ports() -> frozenset[int]:
    """Provisioned io-agent CDP ports plus any port from ``WEB_TAB_CDP_URL``."""
    ports = set(_DEFAULT_CDP_PORTS)
    env_url = os.environ.get("WEB_TAB_CDP_URL", "").strip()
    if env_url:
        env_parts = _safe_urlsplit(env_url)
        env_port = None
        if env_parts is not None:
            try:
                env_port = env_parts.port
            except ValueError:
                env_port = None
        if env_port is not None:
            ports.add(env_port)
    return frozenset(ports)


def _validate_cdp_url(url: str) -> str:
    """Require loopback HTTP CDP on an allowlisted port."""
    raw = str(url).strip()
    parts = _safe_urlsplit(raw)
    if parts is None:
        raise ValueError("invalid_cdp_url:malformed")
    scheme = (parts.scheme or "").lower()
    if scheme != "http":
        raise ValueError(f"invalid_cdp_url:scheme:{scheme or 'missing'}")
    host = (parts.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        raise ValueError(f"invalid_cdp_url:host:{_host_error_suffix(host)}")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("invalid_cdp_url:port:malformed") from exc
    if port is None:
        raise ValueError("invalid_cdp_url:port:missing")
    if port not in _allowed_cdp_ports():
        raise ValueError(f"invalid_cdp_url:port:{port}")
    host_literal = f"[{host}]" if ":" in host else host
    return f"http://{host_literal}:{port}"


def normalize_cdp_store_key(cdp_url: str) -> str:
    """Canonical CDP URL for binding-store keys (localhost → 127.0.0.1)."""
    validated = _validate_cdp_url(str(cdp_url).strip())
    parts = _safe_urlsplit(validated)
    if parts is None:
        return validated.rstrip("/")
    host = (parts.hostname or "").lower()
    port = parts.port
    if host in {"localhost", "::1"}:
        host = "127.0.0.1"
    host_literal = f"[{host}]" if ":" in host else host
    return f"http://{host_literal}:{port}"


def resolve_cdp_url(explicit: str | None) -> str:
    """Return configured CDP endpoint URL; explicit wins over env."""
    if explicit and str(explicit).strip():
        return _validate_cdp_url(str(explicit).strip())
    url = os.environ.get("WEB_TAB_CDP_URL", "").strip()
    if not url:
        raise ValueError("missing_cdp_url")
    return _validate_cdp_url(url)


def resolve_cdp_evaluate_timeout_s() -> float:
    """Finite bound for CDP attach + target match + evaluate (seconds).

    Invalid, nonpositive, or nonfinite ``WEB_TAB_CDP_TIMEOUT_S`` values fall
    back to the default (~10s) rather than disabling the bound.
    """
    raw = os.environ.get("WEB_TAB_CDP_TIMEOUT_S", "").strip()
    if not raw:
        return _DEFAULT_CDP_EVALUATE_TIMEOUT_S
    try:
        val = float(raw)
    except ValueError:
        return _DEFAULT_CDP_EVALUATE_TIMEOUT_S
    if not math.isfinite(val) or val <= 0:
        return _DEFAULT_CDP_EVALUATE_TIMEOUT_S
    return val


def resolve_cdp_cleanup_timeout_s() -> float:
    """Finite bound for Playwright ``stop()`` during attach/evaluate cleanup (seconds).

    Invalid, nonpositive, or nonfinite ``WEB_TAB_CDP_CLEANUP_TIMEOUT_S`` values
    fall back to the default (~1s). Cleanup is separate from the operation
    deadline; uncooperative driver shutdown may still leave a process needing
    recovery when stop ignores cancellation.
    """
    raw = os.environ.get("WEB_TAB_CDP_CLEANUP_TIMEOUT_S", "").strip()
    if not raw:
        return _DEFAULT_CDP_CLEANUP_TIMEOUT_S
    try:
        val = float(raw)
    except ValueError:
        return _DEFAULT_CDP_CLEANUP_TIMEOUT_S
    if not math.isfinite(val) or val <= 0:
        return _DEFAULT_CDP_CLEANUP_TIMEOUT_S
    return val
