"""Concurrent catalog lookups share one failed refresh."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "scope_catalog_under_test",
    Path(__file__).resolve().parent / "handlers" / "scope_catalog.py",
)
assert _SPEC is not None and _SPEC.loader is not None
scope_catalog = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(scope_catalog)


@pytest.fixture(autouse=True)
def _reset_catalog() -> None:
    scope_catalog._cache_lock = asyncio.Lock()
    scope_catalog._cache_scopes = None
    scope_catalog._cache_prefixes = None
    scope_catalog._cache_ts = 0.0
    scope_catalog._cache_last_attempt_ts = 0.0


@pytest.mark.asyncio
async def test_cold_failure_is_one_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """A burst after a cold /scopes failure does not start another timeout each."""
    calls = 0

    async def _fail(_base_url: str) -> bool:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return False

    monkeypatch.setattr(scope_catalog, "_refresh_cache", _fail)
    results = await asyncio.gather(
        *[scope_catalog.fetch_valid_scopes("http://rag") for _ in range(5)]
    )
    assert results == [None, None, None, None, None]
    assert calls == 1


@pytest.mark.asyncio
async def test_slow_refresh_coalesces_while_backoff_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queued callers must not start another refresh when the first exceeds backoff."""
    backoff = 0.08
    monkeypatch.setattr(scope_catalog, "_refresh_backoff_seconds", backoff)
    calls = 0

    async def _slow_fail(_base_url: str) -> bool:
        nonlocal calls
        calls += 1
        await asyncio.sleep(backoff + 0.02)
        return False

    monkeypatch.setattr(scope_catalog, "_refresh_cache", _slow_fail)
    results = await asyncio.gather(
        *[scope_catalog.fetch_valid_scopes("http://rag") for _ in range(5)]
    )
    assert results == [None, None, None, None, None]
    assert calls == 1


@pytest.mark.asyncio
async def test_refresh_exception_still_backoffs_for_concurrent_callers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Waiters during a raising refresh must not each start another /scopes attempt."""
    backoff = 0.08
    monkeypatch.setattr(scope_catalog, "_refresh_backoff_seconds", backoff)
    calls = 0

    async def _slow_raise(_base_url: str) -> bool:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.12)
        raise RuntimeError("rag unavailable")

    monkeypatch.setattr(scope_catalog, "_refresh_cache", _slow_raise)
    results = await asyncio.gather(
        *[scope_catalog.fetch_valid_scopes("http://rag") for _ in range(5)],
        return_exceptions=True,
    )
    assert calls == 1
    assert any(isinstance(r, RuntimeError) for r in results)
    assert all(r is None or isinstance(r, RuntimeError) for r in results)
