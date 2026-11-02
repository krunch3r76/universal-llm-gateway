"""Private configuration for web_tab observation (not generic persona defaults)."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import SplitResult, urlsplit

import yaml

_DEFAULT_CDP_EVALUATE_TIMEOUT_S = 10.0
_DEFAULT_CDP_CLEANUP_TIMEOUT_S = 1.0
_DEFAULT_CDP_PORTS = frozenset({9342, 9343})
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_HOST_ERROR_MAX_LEN = 64
_SURFACE_CDP_ENV = {
    "chatgpt": "WEB_TAB_CDP_URL_CHATGPT",
    "anthropic_console": "WEB_TAB_CDP_URL_ANTHROPIC_CONSOLE",
}
_SURFACE_CDP_ENV_ALIASES = {
    "claude": "WEB_TAB_CDP_URL_CLAUDE",
}
_UNMAPPED_SURFACES = frozenset({"grok", "x_grok", "x"})
WEB_TAB_SMOKE_PROVIDER_FIXTURES: frozenset[str] = frozenset(
    {
        "chatgpt_composer.html",
        "grok_chat.html",
    }
)


@dataclass(frozen=True)
class WebTabHouse:
    surface: str
    cdp_url: str
    display: str
    profile: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def normalize_web_tab_surface(surface: str | None) -> str | None:
    """Map caller surface aliases to canonical keys (chatgpt, anthropic_console, grok) or None."""
    raw = (surface or "").strip().lower()
    if not raw:
        return None
    if raw in {"chatgpt", "openai"}:
        return "chatgpt"
    if raw in {"anthropic_console", "claude-console", "claude"}:
        return "anthropic_console"
    if raw in _UNMAPPED_SURFACES:
        return "grok"
    return raw


@lru_cache(maxsize=1)
def load_web_tab_houses() -> dict[str, WebTabHouse]:
    """Checked-in surface → house mapping (CDP URL, display, profile)."""
    yaml_path = _repo_root() / "config" / "web_tab" / "houses.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("houses.yaml root must be a mapping")
    surfaces = data.get("surfaces") or {}
    if not isinstance(surfaces, dict):
        raise ValueError("houses.yaml surfaces must be a mapping")
    out: dict[str, WebTabHouse] = {}
    for key, row in surfaces.items():
        if not isinstance(row, dict):
            continue
        surf = str(key).strip().lower()
        cdp = str(row.get("cdp_url") or "").strip()
        display = str(row.get("display") or "").strip()
        profile = str(row.get("profile") or "").strip()
        if not surf or not cdp:
            raise ValueError(f"house {key!r} requires surface key and cdp_url")
        out[surf] = WebTabHouse(
            surface=surf,
            cdp_url=cdp,
            display=display,
            profile=profile,
        )
    return out


def _house_cdp_url(house: WebTabHouse) -> str:
    env_keys: list[str] = []
    primary = _SURFACE_CDP_ENV.get(house.surface, "")
    if primary:
        env_keys.append(primary)
    if house.surface == "anthropic_console":
        legacy = _SURFACE_CDP_ENV_ALIASES.get("claude", "")
        if legacy:
            env_keys.append(legacy)
    for env_key in env_keys:
        override = os.environ.get(env_key, "").strip()
        if override:
            return override
    return house.cdp_url


def _cdp_port(url: str) -> int:
    parts = _safe_urlsplit(url)
    if parts is None or parts.port is None:
        raise ValueError("invalid_cdp_url:port:missing")
    return int(parts.port)


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
    """Provisioned io-agent CDP ports plus env overrides and house map."""
    ports = set(_DEFAULT_CDP_PORTS)
    for house in load_web_tab_houses().values():
        parts = _safe_urlsplit(house.cdp_url.strip())
        if parts is not None:
            try:
                p = parts.port
            except ValueError:
                p = None
            if p is not None:
                ports.add(p)
    for env_key in (
        "WEB_TAB_CDP_URL",
        *_SURFACE_CDP_ENV.values(),
        *_SURFACE_CDP_ENV_ALIASES.values(),
    ):
        env_url = os.environ.get(env_key, "").strip()
        if not env_url:
            continue
        env_parts = _safe_urlsplit(env_url)
        if env_parts is None:
            continue
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
    """Return configured CDP endpoint URL; explicit wins over env (no surface map)."""
    return resolve_cdp_url_for_surface(None, explicit)


def resolve_cdp_url_for_surface(
    surface: str | None,
    explicit: str | None,
) -> str:
    """Resolve CDP URL for a product surface with optional explicit override.

    Mapped surfaces (chatgpt, anthropic_console) default to ``config/web_tab/houses.yaml``.
    Explicit URLs on a mapped surface must match the mapped port or raise
    ``cdp_surface_mismatch``. Unmapped surfaces (e.g. grok) require explicit
    URL or ``WEB_TAB_CDP_URL``.
    """
    canon = normalize_web_tab_surface(surface)
    houses = load_web_tab_houses()
    house = houses.get(canon) if canon else None

    explicit_raw = str(explicit).strip() if explicit else ""
    if explicit_raw:
        validated = _validate_cdp_url(explicit_raw)
        if house is not None:
            mapped = _validate_cdp_url(_house_cdp_url(house))
            if _cdp_port(validated) != _cdp_port(mapped):
                raise ValueError("cdp_surface_mismatch")
        return validated

    if house is not None:
        return _validate_cdp_url(_house_cdp_url(house))

    url = os.environ.get("WEB_TAB_CDP_URL", "").strip()
    if not url:
        raise ValueError("missing_cdp_url")
    return _validate_cdp_url(url)


def resolve_cdp_for_web_tab_options(
    *,
    surface: str | None,
    cdp_url: str | None,
) -> str:
    """Pipeline/MCP option bag → validated CDP URL."""
    explicit = str(cdp_url).strip() if cdp_url else None
    if explicit == "":
        explicit = None
    surf = str(surface).strip() if surface else None
    if surf == "":
        surf = None
    return resolve_cdp_url_for_surface(surf, explicit)


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
