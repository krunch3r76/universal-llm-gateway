"""Last-known-good scope catalog when ``/scopes`` refresh fails."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "handlers" / "scope_catalog.py"
_spec = importlib.util.spec_from_file_location("scope_catalog_under_test", _PATH)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)


class _Boom:
    def raise_for_status(self) -> None:
        raise RuntimeError("timeout")


class _Client:
    def __init__(self) -> None:
        self.gets = 0

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, path: str) -> _Boom:
        self.gets += 1
        return _Boom()


def _reset(now: float) -> None:
    _mod._cache_scopes = {"architecture", "code_retrieval"}
    _mod._cache_prefixes = {"architecture": ["docs/architecture/"]}
    _mod._cache_ts = now - 120.0
    _mod._cache_last_attempt_ts = 0.0


def test_refresh_failure_serves_catalog_inside_staleness_window(monkeypatch) -> None:
    client = _Client()
    monkeypatch.setattr(_mod, "make_async_client", lambda *a, **k: client)
    monkeypatch.setattr(_mod.time, "monotonic", lambda: 1_000.0)
    _reset(1_000.0)

    async def _run() -> set[str] | None:
        return await _mod.fetch_valid_scopes("http://rag")

    try:
        got = asyncio.run(_run())
    finally:
        _mod._cache_scopes = None
        _mod._cache_prefixes = None
        _mod._cache_ts = 0.0
        _mod._cache_last_attempt_ts = 0.0
    assert got == {"architecture", "code_retrieval"}
    assert client.gets == 1


def test_refresh_failure_past_staleness_window_returns_none(monkeypatch) -> None:
    client = _Client()
    monkeypatch.setattr(_mod, "make_async_client", lambda *a, **k: client)
    monkeypatch.setattr(_mod.time, "monotonic", lambda: 1_000.0)
    _mod._cache_scopes = {"architecture"}
    _mod._cache_prefixes = {}
    _mod._cache_ts = 1_000.0 - 301.0
    _mod._cache_last_attempt_ts = 0.0

    async def _run() -> set[str] | None:
        return await _mod.fetch_valid_scopes("http://rag")

    try:
        got = asyncio.run(_run())
    finally:
        _mod._cache_scopes = None
        _mod._cache_prefixes = None
        _mod._cache_ts = 0.0
        _mod._cache_last_attempt_ts = 0.0
    assert got is None


def test_backoff_skips_a_second_refresh_while_stale_catalog_is_usable(monkeypatch) -> None:
    client = _Client()
    monkeypatch.setattr(_mod, "make_async_client", lambda *a, **k: client)
    monkeypatch.setattr(_mod.time, "monotonic", lambda: 1_000.0)
    _reset(1_000.0)

    async def _run() -> None:
        assert await _mod.fetch_valid_scopes("http://rag") == {
            "architecture",
            "code_retrieval",
        }
        assert await _mod.fetch_valid_scopes("http://rag") == {
            "architecture",
            "code_retrieval",
        }

    try:
        asyncio.run(_run())
    finally:
        _mod._cache_scopes = None
        _mod._cache_prefixes = None
        _mod._cache_ts = 0.0
        _mod._cache_last_attempt_ts = 0.0
    assert client.gets == 1
