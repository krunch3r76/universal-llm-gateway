"""Runtime scope catalog lookup with short TTL caching.

Provides:
- ``fetch_valid_scopes``: scope name validation (set of names)
- ``fetch_scope_prefixes``: full prefix map for child-scope resolution
- ``resolve_child_scopes``: determines which candidates are children
  of a parent scope via prefix containment
"""

from __future__ import annotations

import asyncio
import time
from typing import cast

from transport_utils.rag_client import make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

_cache_ttl_seconds = 60.0
_cache_lock = asyncio.Lock()
_cache_scopes: set[str] | None = None
_cache_prefixes: dict[str, list[str]] | None = None
_cache_ts: float = 0.0


async def _refresh_cache(base_url: str) -> bool:
    """Fetch ``/scopes`` and populate both name and prefix caches.

    Returns True on success, False on failure.
    """
    global _cache_scopes, _cache_prefixes, _cache_ts  # noqa: PLW0603

    try:
        async with make_async_client(base_url, timeout=5.0) as client:
            response = await client.get("/scopes")
        _ = response.raise_for_status()
        payload_obj = cast(object, response.json())
        if not isinstance(payload_obj, dict):
            logger.warning(
                "RAG /scopes returned invalid payload type: %s",
                type(payload_obj).__name__,
            )
            return False
        if not all(isinstance(key, str) for key in payload_obj.keys()):
            logger.warning("RAG /scopes returned non-string payload keys")
            return False
        payload = cast(dict[str, object], payload_obj)
        scopes_raw_obj: object = payload.get("scopes")
        if not isinstance(scopes_raw_obj, dict):
            logger.warning(
                "RAG /scopes returned invalid scopes field type: %s",
                type(scopes_raw_obj).__name__,
            )
            return False
        scopes_map = cast(dict[str, object], scopes_raw_obj)
        if not all(isinstance(key, str) for key in scopes_map.keys()):
            logger.warning("RAG /scopes returned non-string scope keys")
            return False

        typed_map = cast(dict[str, object], scopes_map)
        names: set[str] = set()
        prefixes: dict[str, list[str]] = {}
        for name, info in typed_map.items():
            if not name:
                continue
            names.add(name)
            if isinstance(info, dict):
                raw_pfx = info.get("prefixes")
                if isinstance(raw_pfx, list):
                    prefixes[name] = [str(p) for p in raw_pfx if isinstance(p, str)]
                else:
                    logger.warning(
                        "RAG /scopes returned non-list prefixes for scope %s",
                        name,
                    )
        if not names:
            logger.warning("RAG /scopes returned empty scope catalog")
            return False

        _cache_scopes = names
        _cache_prefixes = prefixes
        _cache_ts = time.monotonic()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("RAG scope catalog fetch failed: %s", exc)
        return False


async def _ensure_cache(base_url: str) -> bool:
    """Ensure cache is fresh; returns True if cache is valid."""
    now = time.monotonic()
    if _cache_scopes is not None and (now - _cache_ts) < _cache_ttl_seconds:
        return True
    async with _cache_lock:
        now2 = time.monotonic()
        if _cache_scopes is not None and (now2 - _cache_ts) < _cache_ttl_seconds:
            return True
        return await _refresh_cache(base_url)


async def fetch_valid_scopes(base_url: str) -> set[str] | None:
    """Return valid scope names from RAG ``/scopes`` with TTL cache.

    Callers enforce fail-closed behavior: ``None`` means scope validation
    cannot proceed and retrieval should return zero chunks.
    """
    if await _ensure_cache(base_url):
        return _cache_scopes
    return None


async def fetch_scope_prefixes(base_url: str) -> dict[str, list[str]] | None:
    """Return scope → prefix list mapping with TTL cache.

    Used for child-scope resolution (prefix containment checks).
    """
    if await _ensure_cache(base_url):
        return _cache_prefixes
    return None


def resolve_child_scopes(
    parent: str,
    candidates: list[str],
    prefix_map: dict[str, list[str]],
) -> list[str]:
    """Return candidates whose prefixes are fully contained by *parent*.

    A candidate scope C is a child of parent P when ∀ prefix in C:
    ∃ prefix in P such that C_prefix starts with P_prefix.
    This handles both exact matches and directory containment.
    """
    parent_prefixes = prefix_map.get(parent, [])
    if not parent_prefixes:
        return []
    children: list[str] = []
    for name in candidates:
        if name == parent:
            continue
        child_prefixes = prefix_map.get(name, [])
        if not child_prefixes:
            continue
        if all(
            any(cp.startswith(pp) for pp in parent_prefixes) for cp in child_prefixes
        ):
            children.append(name)
    return children
