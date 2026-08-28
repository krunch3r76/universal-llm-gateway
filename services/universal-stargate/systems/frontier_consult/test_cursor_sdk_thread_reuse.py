"""Tests for cursor-sdk thread reuse resolution (3-tuple + auto-consolidation flag)."""

from __future__ import annotations

import pytest

from .admission import FrontierEndpointError
from .cursor_sdk_thread_reuse import (
    CONDUCTOR_COORD_SPLIT_CODE,
    CONDUCTOR_COORD_SPLIT_HINT,
    CURSOR_WORKER_THREAD_OCCUPIED,
    api_split_warning,
    consolidation_split_warning,
    refuse_occupied_worker_thread,
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


@pytest.mark.asyncio
async def test_conductor_empty_non_pending_raises_coord_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _probe(thread_id: str) -> dict | None:
        if thread_id == "9676":
            return {
                "bus_lifecycle_state": None,
                "turn_count": 0,
                "parent_thread": "9582",
                "tags": [],
            }
        return None

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.probe_thread",
        _probe,
    )
    with pytest.raises(FrontierEndpointError) as excinfo:
        await resolve_cursor_sdk_thread_targets(
            reuse_thread=None,
            dispatch_thread_id="9676",
            packet_kind="conductor",
            request_id="req-split",
        )
    err = excinfo.value
    assert err.code == CONDUCTOR_COORD_SPLIT_CODE
    assert err.status_code == 422
    assert err.details is not None
    assert "reuse_thread=" in err.details["hint"]
    assert "reuse_thread=" in CONDUCTOR_COORD_SPLIT_HINT


@pytest.mark.asyncio
async def test_conductor_pending_empty_child_reuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _probe(thread_id: str) -> dict | None:
        if thread_id == "9001":
            return {
                "bus_lifecycle_state": "pending",
                "turn_count": 0,
                "parent_thread": "9582",
                "tags": [],
            }
        return None

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.probe_thread",
        _probe,
    )
    reuse, parent, is_auto = await resolve_cursor_sdk_thread_targets(
        reuse_thread=None,
        dispatch_thread_id="9001",
        packet_kind="conductor",
        request_id="req-pending",
    )
    assert reuse == "9001"
    assert parent is None
    assert is_auto is True


@pytest.mark.asyncio
async def test_conductor_root_with_turns_mints_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _probe(thread_id: str) -> dict | None:
        if thread_id == "9582":
            return {
                "bus_lifecycle_state": None,
                "turn_count": 12,
                "parent_thread": None,
                "tags": ["role:root"],
            }
        return None

    async def _not_pending(_thread_id: str) -> bool:
        return False

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.probe_thread",
        _probe,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.is_pending_empty_worker_thread",
        _not_pending,
    )
    reuse, parent, is_auto = await resolve_cursor_sdk_thread_targets(
        reuse_thread=None,
        dispatch_thread_id="9582",
        packet_kind="conductor",
        request_id="req-root",
    )
    assert reuse is None
    assert parent == "9582"
    assert is_auto is False


@pytest.mark.asyncio
async def test_conductor_reuse_thread_work_thread_does_not_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _probe(thread_id: str) -> dict | None:
        return {"turn_count": 4, "bus_lifecycle_state": "failed", "tags": []}

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.probe_thread",
        _probe,
    )
    reuse, parent, is_auto = await resolve_cursor_sdk_thread_targets(
        reuse_thread="9677",
        dispatch_thread_id="9582",
        packet_kind="conductor",
        request_id="req-readmit",
    )
    assert reuse == "9677"
    assert parent == "9582"
    assert is_auto is False


@pytest.mark.asyncio
async def test_non_conductor_active_empty_still_splits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _not_pending(_thread_id: str) -> bool:
        return False

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.is_pending_empty_worker_thread",
        _not_pending,
    )
    reuse, parent, is_auto = await resolve_cursor_sdk_thread_targets(
        reuse_thread=None,
        dispatch_thread_id="2683",
        packet_kind=None,
    )
    assert reuse is None
    assert parent == "2683"
    assert is_auto is False


@pytest.mark.asyncio
async def test_conductor_pending_empty_root_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _probe(thread_id: str) -> dict | None:
        if thread_id == "9100":
            return {
                "bus_lifecycle_state": "pending",
                "turn_count": 0,
                "parent_thread": None,
                "tags": ["role:root"],
            }
        return None

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.probe_thread",
        _probe,
    )
    with pytest.raises(FrontierEndpointError) as excinfo:
        await resolve_cursor_sdk_thread_targets(
            reuse_thread=None,
            dispatch_thread_id="9100",
            packet_kind="conductor",
            request_id="req-pending-root",
        )
    assert excinfo.value.code == CONDUCTOR_COORD_SPLIT_CODE


def test_api_split_warning_on_non_reusable_active_arc() -> None:
    msg = api_split_warning(
        reuse_thread=None,
        parent_dispatch_thread_id="6001",
        split_thread=False,
    )
    assert msg is not None
    assert "6001" in msg
    assert "split_thread=true" in msg


@pytest.mark.asyncio
async def test_refuse_occupied_skips_nest_under() -> None:
    await refuse_occupied_worker_thread(
        request_id="req-nest",
        reuse_thread="9675",
        nest_under="parent-disp",
    )


@pytest.mark.asyncio
async def test_refuse_occupied_skips_read_only() -> None:
    await refuse_occupied_worker_thread(
        request_id="req-ro",
        reuse_thread="9675",
        nest_under=None,
        read_only=True,
    )


@pytest.mark.asyncio
async def test_refuse_occupied_skips_pending_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _probe(thread_id: str) -> dict | None:
        return {"bus_lifecycle_state": "pending", "turn_count": 0}

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.probe_thread",
        _probe,
    )
    await refuse_occupied_worker_thread(
        request_id="req-empty",
        reuse_thread="9100",
        nest_under=None,
    )


@pytest.mark.asyncio
async def test_refuse_occupied_live_status_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _probe(thread_id: str) -> dict | None:
        return {"bus_lifecycle_state": "admitted", "turn_count": 6}

    class _Resp:
        status_code = 200

        def json(self) -> dict:
            return {"status": "running", "dispatch_id": "85e312e900aa-26c192cf"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params=None):
            _ = url, params
            return _Resp()

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.probe_thread",
        _probe,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.make_async_client",
        lambda *a, **k: _Client(),
    )
    with pytest.raises(FrontierEndpointError) as excinfo:
        await refuse_occupied_worker_thread(
            request_id="req-occ",
            reuse_thread="9675",
            nest_under=None,
        )
    assert excinfo.value.code == CURSOR_WORKER_THREAD_OCCUPIED
    assert excinfo.value.details["holder_dispatch_id"] == "85e312e900aa-26c192cf"
