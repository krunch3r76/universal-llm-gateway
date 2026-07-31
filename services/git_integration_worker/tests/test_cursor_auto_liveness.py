"""cursor-auto liveness — prune must not permanently kill the handler."""

from __future__ import annotations

from services.git_integration_worker.cursor_auto.liveness import AutoLivenessRegistry


def test_heartbeat_reregisters_after_ttl_prune():
    reg = AutoLivenessRegistry(heartbeat_ttl_s=0.01)
    reg.register("h1")
    assert reg.is_live() is True
    # Simulate mid-job silence longer than TTL, then a probe that prunes.
    import time

    time.sleep(0.02)
    assert reg.is_live() is False  # pruned
    existed = reg.heartbeat("h1")
    assert existed is False  # was missing
    assert reg.is_live() is True  # resurrected
