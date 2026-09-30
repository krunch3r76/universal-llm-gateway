"""Tests for cursor-sdk thread reuse resolution (3-tuple + auto-consolidation flag)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import Response
from fastapi.responses import JSONResponse

from .admission import FrontierEndpointError
from .cursor_sdk_thread_reuse import (
    CONDUCTOR_COORD_SPLIT_CODE,
    CONDUCTOR_COORD_SPLIT_HINT,
    CONDUCTOR_SUMMONING_OPERATOR_LANE_CODE,
    CONDUCTOR_SUMMONING_OPERATOR_LANE_HINT,
    CURSOR_WORKER_THREAD_OCCUPIED,
    api_split_warning,
    consolidation_split_warning,
    probe_continuity_root_thread_id,
    refuse_conductor_coord_split,
    refuse_conductor_operator_lane_summon,
    refuse_occupied_worker_thread,
    resolve_cursor_sdk_thread_targets,
    resolve_generate_thread_targets,
)
from .generate_wrap import (
    GenerateWrapResult,
    dispatch_cursor_sdk_generate_route,
)
from .route import TeamDispatchGenerateBody


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
            contract="conductor",
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
            contract="conductor",
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


@pytest.mark.asyncio
async def test_probe_continuity_root_thread_id_role_root_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _probe(thread_id: str) -> dict | None:
        return {"turn_count": 3, "tags": ["role:root"]}

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.probe_thread",
        _probe,
    )
    assert await probe_continuity_root_thread_id("9758") == "9758"


@pytest.mark.asyncio
async def test_probe_continuity_root_thread_id_legacy_root_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _probe(thread_id: str) -> dict | None:
        return {"turn_count": 2, "tags": [], "parent_thread": None}

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.probe_thread",
        _probe,
    )
    assert await probe_continuity_root_thread_id("9758") == "9758"


@pytest.mark.asyncio
async def test_probe_continuity_root_thread_id_zero_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _probe(thread_id: str) -> dict | None:
        return {"turn_count": 0, "tags": ["role:root"]}

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.probe_thread",
        _probe,
    )
    assert await probe_continuity_root_thread_id("9758") is None


@pytest.mark.asyncio
async def test_probe_continuity_root_thread_id_probe_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _probe(_thread_id: str) -> dict | None:
        return None

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.probe_thread",
        _probe,
    )
    assert await probe_continuity_root_thread_id("9758") is None


@pytest.mark.asyncio
async def test_probe_continuity_root_thread_id_short_circuits_non_digit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def _probe(thread_id: str) -> dict | None:
        calls.append(thread_id)
        return {"turn_count": 3, "tags": ["role:root"]}

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.probe_thread",
        _probe,
    )
    assert await probe_continuity_root_thread_id(None) is None
    assert await probe_continuity_root_thread_id("") is None
    assert await probe_continuity_root_thread_id("my-slug-thread") is None
    assert calls == []


_SHAPE2_HINT = (
    "Pre-create a pending-empty child with parent_thread=<lane>, then pass it "
    "as dispatch_thread_id and reuse_thread."
)
_REPO_ROOT = Path(__file__).resolve().parents[4]


def _registry_read(*, present: bool, data: dict | None = None):
    from claude_bundles.cdp_registry_store import RegistryRead

    return RegistryRead(
        data=data or {},
        observed_home_kind="dispatch",
        observed_home=Path("/tmp/dispatch-home"),
        source_path=Path("/tmp/dispatch-home/.gateway/cdp-registry/sessions.json"),
        present=present,
    )


def _patch_probe(monkeypatch: pytest.MonkeyPatch, payload: dict | None) -> None:
    async def _probe(_thread_id: str) -> dict | None:
        return payload

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.probe_thread",
        _probe,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.probe_thread",
        _probe,
    )


def _install_route_stubs(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, int], dict]:
    import systems.frontier_consult.generate_wrap as generate_wrap

    calls = {"prepare": 0, "resolve": 0}
    captured: dict = {}

    def _prepare(**kwargs: object) -> GenerateWrapResult:
        calls["prepare"] += 1
        captured.update(kwargs)
        return GenerateWrapResult(
            packet_path="tmp/reviews/conductor-packet.md",
            materialized=True,
            warnings=[],
        )

    real_resolve = generate_wrap.resolve_cursor_sdk_thread_targets

    async def _resolve(**kwargs: object):
        calls["resolve"] += 1
        return await real_resolve(**kwargs)

    async def _dispatch(**_kwargs: object) -> dict:
        return {"status": "admitted"}

    monkeypatch.setattr(generate_wrap, "prepare_conductor_packet", _prepare)
    monkeypatch.setattr(generate_wrap, "resolve_cursor_sdk_thread_targets", _resolve)
    monkeypatch.setattr(generate_wrap, "dispatch_cursor_sdk_generate", _dispatch)
    return calls, captured


async def _conductor_route(
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload: dict | None,
    dispatch_thread_id: str = "13643",
    reuse_thread: str | None = None,
) -> tuple[object, dict[str, int], dict]:
    _patch_probe(monkeypatch, payload)
    calls, captured = _install_route_stubs(monkeypatch)
    body = TeamDispatchGenerateBody(
        op="generate",
        contract="conductor",
        source_ref="todo:seed-conductor-own-worker-summon",
        lane="B",
        caller_agent="cursor",
        dispatch_thread_id=dispatch_thread_id,
        reuse_thread=reuse_thread,
    )
    result = await dispatch_cursor_sdk_generate_route(
        request_id="req-op-lane",
        body=body,
        seat="cursor-sdk",
        response=Response(),
    )
    return result, calls, captured


def _patch_session(
    monkeypatch: pytest.MonkeyPatch,
    *,
    read,
    found,
) -> None:
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_sessions_read",
        read,
    )
    monkeypatch.setattr(
        "claude_bundles.cse_session_common.find_session_by_registration",
        found,
    )


def _assert_operator_lane_422(result: object, calls: dict[str, int]) -> None:
    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    body = json.loads(result.body)
    assert body["error"]["code"] == CONDUCTOR_SUMMONING_OPERATOR_LANE_CODE
    assert body["field"] == "dispatch_thread_id"
    assert body["details"]["hint"] == _SHAPE2_HINT
    assert CONDUCTOR_SUMMONING_OPERATOR_LANE_HINT == _SHAPE2_HINT
    assert calls["prepare"] == 0
    assert calls["resolve"] == 0


_OPERATOR_PAYLOAD = {
    "cse_registration_id": "reg-op",
    "turn_count": 4,
    "parent_thread": None,
    "tags": ["role:root"],
    "bus_lifecycle_state": None,
}


@pytest.mark.asyncio
async def test_operator_proxy_refuses_before_materialize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {
        "purpose": "operator-proxy",
        "ids": {"registration_id": "reg-op", "lane_thread": "12286"},
    }
    _patch_session(
        monkeypatch,
        read=lambda: _registry_read(present=True, data={"s": row}),
        found=lambda _sessions, _reg: ("reg:op", row),
    )
    result, calls, _captured = await _conductor_route(
        monkeypatch,
        payload=_OPERATOR_PAYLOAD,
        dispatch_thread_id="12286",
    )
    _assert_operator_lane_422(result, calls)


@pytest.mark.asyncio
async def test_operator_proxy_refuses_when_reuse_thread_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {
        "purpose": "operator-proxy",
        "ids": {"registration_id": "reg-op", "lane_thread": "12286"},
    }
    _patch_session(
        monkeypatch,
        read=lambda: _registry_read(present=True, data={"s": row}),
        found=lambda _sessions, _reg: ("reg:op", row),
    )
    result, calls, _captured = await _conductor_route(
        monkeypatch,
        payload=_OPERATOR_PAYLOAD,
        dispatch_thread_id="12286",
        reuse_thread="12286",
    )
    _assert_operator_lane_422(result, calls)


@pytest.mark.asyncio
async def test_review_purpose_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {"purpose": "review", "ids": {"lane_thread": "12286"}}
    _patch_probe(monkeypatch, {"cse_registration_id": "reg-review"})
    _patch_session(
        monkeypatch,
        read=lambda: _registry_read(present=True),
        found=lambda _sessions, _reg: ("reg:review", row),
    )
    assert row["purpose"] == "review"
    result = await refuse_conductor_operator_lane_summon(
        request_id="req-review",
        contract="conductor",
        dispatch_thread_id="12286",
    )
    assert result is None


@pytest.mark.asyncio
async def test_no_purpose_matching_lane_thread_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {"ids": {"lane_thread": "12286"}}
    _patch_probe(monkeypatch, {"cse_registration_id": "reg-holder"})
    _patch_session(
        monkeypatch,
        read=lambda: _registry_read(present=True),
        found=lambda _sessions, _reg: ("reg:holder", row),
    )
    with pytest.raises(FrontierEndpointError) as excinfo:
        await refuse_conductor_operator_lane_summon(
            request_id="req-holder",
            contract="conductor",
            dispatch_thread_id="12286",
        )
    assert excinfo.value.code == CONDUCTOR_SUMMONING_OPERATOR_LANE_CODE
    assert excinfo.value.details is not None
    assert excinfo.value.details["hint"] == _SHAPE2_HINT


@pytest.mark.asyncio
async def test_pending_empty_child_summons_on_child_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, calls, captured = await _conductor_route(
        monkeypatch,
        payload={
            "bus_lifecycle_state": "pending",
            "turn_count": 0,
            "parent_thread": "12286",
            "cse_registration_id": None,
        },
        dispatch_thread_id="13643",
        reuse_thread="13643",
    )
    assert isinstance(result, dict)
    assert calls["prepare"] == 1
    assert captured["summoning_thread_id"] == "13643"


@pytest.mark.asyncio
async def test_probe_miss_warns_and_coord_split_stays_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _calls, _captured = await _conductor_route(
        monkeypatch,
        payload=None,
        dispatch_thread_id="13643",
        reuse_thread="13643",
    )
    assert isinstance(result, dict)
    warnings = result.get("warnings") or []
    assert any("operator-lane summon check skipped" in item for item in warnings)
    with pytest.raises(FrontierEndpointError) as excinfo:
        await refuse_conductor_coord_split(
            request_id="req-split-miss",
            contract="conductor",
            reuse_thread=None,
            dispatch_thread_id="13643",
        )
    assert excinfo.value.code == CONDUCTOR_COORD_SPLIT_CODE


@pytest.mark.asyncio
async def test_sessions_registry_unreadable_warns_on_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> None:
        raise OSError("unreadable")

    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_sessions_read",
        _boom,
    )
    result, calls, _captured = await _conductor_route(
        monkeypatch,
        payload=_OPERATOR_PAYLOAD,
        dispatch_thread_id="12286",
    )
    assert isinstance(result, dict)
    assert calls["prepare"] == 1
    warnings = result.get("warnings") or []
    assert any("sessions registry unreadable" in item for item in warnings)


@pytest.mark.asyncio
async def test_sessions_registry_absent_warns_observed_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_sessions_read",
        lambda: _registry_read(present=False),
    )
    result, _calls, _captured = await _conductor_route(
        monkeypatch,
        payload=_OPERATOR_PAYLOAD,
        dispatch_thread_id="12286",
    )
    assert isinstance(result, dict)
    warnings = result.get("warnings") or []
    assert any("observed_home_kind=" in item for item in warnings)


@pytest.mark.asyncio
async def test_missing_session_row_is_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_probe(monkeypatch, _OPERATOR_PAYLOAD)
    _patch_session(
        monkeypatch,
        read=lambda: _registry_read(present=True, data={}),
        found=lambda _sessions, _reg: None,
    )
    direct = await refuse_conductor_operator_lane_summon(
        request_id="req-quiet",
        contract="conductor",
        dispatch_thread_id="12286",
    )
    assert direct is None
    result, _calls, _captured = await _conductor_route(
        monkeypatch,
        payload=_OPERATOR_PAYLOAD,
        dispatch_thread_id="12286",
    )
    assert isinstance(result, dict)
    warnings = result.get("warnings") or []
    assert not any("observed_home_kind=" in item for item in warnings)


def test_operator_lane_check_imports_predicate_not_purpose_sets() -> None:
    reuse = (
        _REPO_ROOT
        / "services/universal-stargate/systems/frontier_consult"
        / "cursor_sdk_thread_reuse.py"
    ).read_text(encoding="utf-8")
    assert "is_operator_proxy_mission_purpose" in reuse
    assert "OPERATOR_PROXY_MISSION_PURPOSES" not in reuse
    assert "_GATE_OCCUPANCY_PURPOSES" not in reuse
    purposes = (_REPO_ROOT / "libs/claude_bundles/operator_proxy_mission.py").read_text(
        encoding="utf-8"
    )
    assert '{"operator-proxy", "mission", "operator_proxy"}' in purposes
    occupancy = '{"review", "operator-proxy", "operator_proxy", "mission"}'
    cdp = (
        _REPO_ROOT
        / "services/universal-stargate/systems/frontier_consult/cdp_generate.py"
    ).read_text(encoding="utf-8")
    gate = (
        _REPO_ROOT
        / "services/git_integration_worker/cursor_sdk_closeout"
        / "conductor_exit_reasons.py"
    ).read_text(encoding="utf-8")
    assert occupancy in cdp
    assert occupancy in gate
