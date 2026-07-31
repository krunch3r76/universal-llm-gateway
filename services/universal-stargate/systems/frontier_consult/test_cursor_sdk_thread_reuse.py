"""Tests for cursor-sdk thread reuse resolution (3-tuple + auto-consolidation flag)."""

from __future__ import annotations

import pytest

from .cursor_sdk_thread_reuse import (
    api_split_warning,
    consolidation_split_warning,
    resolve_cursor_sdk_thread_targets,
    resolve_generate_thread_targets,
)


@pytest.mark.asyncio
async def test_explicit_reuse_same_as_arc_is_not_auto_consolidation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _probe(thread_id: str) -> dict | None:
        return {"turn_count": 0}

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.probe_thread",
        _probe,
    )
    reuse, parent, is_auto = await resolve_cursor_sdk_thread_targets(
        reuse_thread="2683",
        dispatch_thread_id="2683",
    )
    assert reuse == "2683"
    assert parent is None
    assert is_auto is False


@pytest.mark.asyncio
async def test_auto_consolidation_on_pending_empty_arc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _pending(_thread_id: str) -> bool:
        return _thread_id == "9001"

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.is_pending_empty_worker_thread",
        _pending,
    )
    reuse, parent, is_auto = await resolve_cursor_sdk_thread_targets(
        reuse_thread=None,
        dispatch_thread_id="9001",
    )
    assert reuse == "9001"
    assert parent is None
    assert is_auto is True


@pytest.mark.asyncio
async def test_active_arc_mints_sibling_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _not_pending(_thread_id: str) -> bool:
        return False

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.is_pending_empty_worker_thread",
        _not_pending,
    )
    reuse, parent, is_auto = await resolve_cursor_sdk_thread_targets(
        reuse_thread=None,
        dispatch_thread_id="2683",
    )
    assert reuse is None
    assert parent == "2683"
    assert is_auto is False
    assert consolidation_split_warning(
        reuse_thread=reuse,
        parent_dispatch_thread_id=parent,
    )


@pytest.mark.asyncio
async def test_explicit_reuse_with_distinct_coord_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _probe(thread_id: str) -> dict | None:
        return {"turn_count": 2}

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.probe_thread",
        _probe,
    )
    reuse, parent, is_auto = await resolve_cursor_sdk_thread_targets(
        reuse_thread="2700",
        dispatch_thread_id="2683",
    )
    assert reuse == "2700"
    assert parent == "2683"
    assert is_auto is False


@pytest.mark.asyncio
async def test_api_lane_reuses_active_numeric_dispatch_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _probe(thread_id: str) -> dict | None:
        if thread_id == "5001":
            return {"status": "active", "turn_count": 1}
        return None

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.probe_thread",
        _probe,
    )
    reuse, parent, is_auto, reuse_after_turn = await resolve_generate_thread_targets(
        reuse_thread=None,
        dispatch_thread_id="5001",
        role_lane="api",
    )
    assert reuse == "5001"
    assert parent is None
    assert is_auto is True
    assert reuse_after_turn == 1


@pytest.mark.asyncio
async def test_api_lane_slug_dispatch_id_no_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fail_probe(_thread_id: str) -> dict | None:
        raise AssertionError("probe should not run for slug dispatch id")

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.probe_thread",
        _fail_probe,
    )
    reuse, parent, is_auto, reuse_after_turn = await resolve_generate_thread_targets(
        reuse_thread=None,
        dispatch_thread_id="thread:dispatch:test",
        role_lane="api",
    )
    assert reuse is None
    assert parent == "thread:dispatch:test"
    assert is_auto is False
    assert reuse_after_turn == 0


@pytest.mark.asyncio
async def test_api_lane_zero_turn_count_no_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _probe(thread_id: str) -> dict | None:
        if thread_id == "5002":
            return {"status": "active", "turn_count": 0}
        return None

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.probe_thread",
        _probe,
    )
    reuse, parent, is_auto, reuse_after_turn = await resolve_generate_thread_targets(
        reuse_thread=None,
        dispatch_thread_id="5002",
        role_lane="api",
    )
    assert reuse is None
    assert parent == "5002"
    assert is_auto is False
    assert reuse_after_turn == 0


@pytest.mark.asyncio
async def test_api_lane_split_thread_opt_out_no_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _probe(thread_id: str) -> dict | None:
        if thread_id == "5003":
            return {"status": "active", "turn_count": 2}
        return None

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.probe_thread",
        _probe,
    )
    reuse, parent, is_auto, reuse_after_turn = await resolve_generate_thread_targets(
        reuse_thread=None,
        dispatch_thread_id="5003",
        role_lane="api",
        split_thread=True,
    )
    assert reuse is None
    assert parent == "5003"
    assert is_auto is False
    assert reuse_after_turn == 0
    assert (
        api_split_warning(
            reuse_thread=reuse,
            parent_dispatch_thread_id=parent,
            split_thread=True,
        )
        is None
    )


@pytest.mark.asyncio
async def test_api_lane_explicit_reuse_probes_turn_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _probe(thread_id: str) -> dict | None:
        if thread_id == "5100":
            return {"status": "active", "turn_count": 3}
        return None

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.probe_thread",
        _probe,
    )
    reuse, parent, is_auto, reuse_after_turn = await resolve_generate_thread_targets(
        reuse_thread="5100",
        dispatch_thread_id="5001",
        role_lane="api",
    )
    assert reuse == "5100"
    assert parent == "5001"
    assert is_auto is False
    assert reuse_after_turn == 3


def test_api_split_warning_on_non_reusable_active_arc() -> None:
    msg = api_split_warning(
        reuse_thread=None,
        parent_dispatch_thread_id="6001",
        split_thread=False,
    )
    assert msg is not None
    assert "6001" in msg
    assert "split_thread=true" in msg
