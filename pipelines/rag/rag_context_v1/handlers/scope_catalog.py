"""Runtime scope catalog lookup with short TTL caching."""

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
_cache_ts: float = 0.0


async def fetch_valid_scopes(base_url: str) -> set[str] | None:
    """Return valid scope names from RAG ``/scopes`` with TTL cache.

    Callers enforce fail-closed behavior: ``None`` means scope validation
    cannot proceed and retrieval should return zero chunks.
    """
    global _cache_scopes, _cache_ts  # noqa: PLW0603

    now = time.monotonic()
    if _cache_scopes is not None and (now - _cache_ts) < _cache_ttl_seconds:
        return _cache_scopes

    async with _cache_lock:
        now2 = time.monotonic()
        if _cache_scopes is not None and (now2 - _cache_ts) < _cache_ttl_seconds:
            return _cache_scopes
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
                return None
            payload_map = cast(dict[object, object], payload_obj)
            if not all(isinstance(key, str) for key in payload_map.keys()):
                logger.warning("RAG /scopes returned non-string payload keys")
                return None
            payload = cast(dict[str, object], payload_map)
            scopes_raw_obj: object = payload.get("scopes")
            if not isinstance(scopes_raw_obj, dict):
                logger.warning(
                    "RAG /scopes returned invalid scopes field type: %s",
                    type(scopes_raw_obj).__name__,
                )
                return None
            scopes_map = cast(dict[object, object], scopes_raw_obj)
            if not all(isinstance(key, str) for key in scopes_map.keys()):
                logger.warning("RAG /scopes returned non-string scope keys")
                return None
            scopes = {key for key in cast(dict[str, object], scopes_map).keys() if key}
            if not scopes:
                logger.warning("RAG /scopes returned empty scope catalog")
                return None
            _cache_scopes = scopes
            _cache_ts = time.monotonic()
            return scopes
        except Exception as exc:  # noqa: BLE001
            logger.warning("RAG scope catalog fetch failed: %s", exc)
            return None
