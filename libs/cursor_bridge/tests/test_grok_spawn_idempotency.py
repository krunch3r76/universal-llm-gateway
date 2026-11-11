"""Concurrency and ordering tests for grok spawn idempotency slots."""

from __future__ import annotations

import concurrent.futures

import pytest
from cursor_bridge.grok_spawn_idempotency import (
    begin,
    clear_spawn_cache_for_tests,
    mark_steer,
    release,
)

pytestmark = pytest.mark.offline


def _prepare_create_unknown(key: str, digest: str) -> str:
    first = begin(key, digest)
    assert first["action"] == "create"
    token = str(first["token"])
    release(key, token, outcome="create_unknown")
    return token


def test_concurrent_adopt_on_create_unknown_one_adopt_one_in_flight() -> None:
    """Two retries on create_unknown: only one adopt; peer gets idempotency_in_flight."""
    clear_spawn_cache_for_tests()
    key = "concurrent-adopt-key"
    digest = "d" * 16
    _prepare_create_unknown(key, digest)

    def _claim() -> dict:
        return begin(key, digest)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        fut_a, fut_b = pool.submit(_claim), pool.submit(_claim)
        a, b = fut_a.result(), fut_b.result()

    actions = sorted([str(a.get("action")), str(b.get("action"))])
    assert actions == ["adopt", "in_flight"]
    codes = [r.get("code") for r in (a, b) if r.get("action") == "in_flight"]
    assert codes == ["idempotency_in_flight"]

    winner = a if a.get("action") == "adopt" else b
    token = str(winner["token"])
    mark_steer(key, token, "attempting")
    mark_steer(key, token, "sent")
    release(key, token, outcome="created")
    retry = begin(key, digest)
    assert retry["action"] != "adopt"
